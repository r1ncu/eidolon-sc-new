# 一只 QQ 群里的冬优子：Docker 镜像
#
# 零第三方依赖，所以这个镜像里没有 `pip install` 那一步——只有 tzdata（ZoneInfo 需要它，
# 否则她会在半夜说「おはよう」）和证书。
#
# 构建：  docker build -t scbot-new:1.2.0 .
# 运行：  见 docker-compose.yml 与 DEPLOY.md

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# 数据在卷里，代码只读。10001 是 scbot 用户，卷第一次挂载时会继承这里的属主。
RUN mkdir -p /data/media \
 && useradd -r -u 10001 -m scbot \
 && chown -R scbot:scbot /app /data

USER scbot

ENV SCCORE_HOST=0.0.0.0 \
    SCCORE_PORT=8100 \
    SCCORE_DB=/data/scbot.db \
    SCCORE_MEDIA=/data/media

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('SCCORE_PORT','8100') + '/api/roster', timeout=3)"

CMD ["python", "serve.py"]
