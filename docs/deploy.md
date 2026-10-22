# 部署

## 需要什么

- Python 3.11 或更高（Debian 12 / Ubuntu 22.04 自带的就够），**不需要安装任何第三方包**。
- 一个目录（代码约 10MB，主要是角色头像与礼物插画）和一份可写的 SQLite。
- 一个 OpenAI 兼容的模型服务与它的 key；不想花钱就先 `--fake` 跑。
- 可选：一个 OpenAI 兼容的 `/embeddings` 服务，只有角色资料的**向量版**需要；不配就用数据版（整份贴入）或者不用资料，见下面「角色的资料：数据版还是向量版」。

一台 1 核 1G 的小机器足够：它一次只处理几个人的回合，图片存在本地磁盘上。

## 装

```bash
sudo useradd --system --create-home --home-dir /opt/scbot-new scbot
sudo -u scbot git clone <这个仓库> /opt/scbot-new        # 或者解压一份 tar
cd /opt/scbot-new
sudo -u scbot cp .env.example .env
sudo -u scbot editor .env                               # 填三个 CHAT_LLM_*
sudo -u scbot mkdir -p /var/lib/scbot/media
```

`.env` 里至少要有：

```
CHAT_LLM_BASE_URL=https://你的服务商/v1
CHAT_LLM_API_KEY=...
CHAT_LLM_MODEL=...
SCCORE_DB=/var/lib/scbot/scbot.db
SCCORE_MEDIA=/var/lib/scbot/media
```

先在前台跑一次，确认能起来、页面能开：

```bash
sudo -u scbot python3 serve.py
# scbot-new  ←  http://0.0.0.0:8100  （28 位角色 / characters, quota, … / 模型名）
```

## 让它常驻

`deploy/scbot-new.service` 是一个可以直接用的 systemd 单元（按需改路径与用户）：

```bash
sudo cp deploy/scbot-new.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scbot-new
sudo systemctl status scbot-new
journalctl -u scbot-new -f
```

单元里做的几件事：`Restart=always`（进程崩了自动起来）、`WorkingDirectory` 指向代码目录（`.env` 与 `characters/` 都是相对它找的）、`StateDirectory` 建好库与图片的目录。

## 放在网上之前

这个服务**没有登录**：谁能访问端口，谁就是那个「制作人」（能看到全部对话与记忆）。所以：

- 默认只在家庭网络里用，别直接映射到公网。
- 要出门用，就套一层带认证的反向代理，或者用 tailscale / wireguard 这类点对点的方案。
- 反向代理要放行 SSE：`proxy_buffering off`、`proxy_read_timeout` 至少要几分钟（一轮回复可能写两三分钟）。

nginx 的骨架：

```nginx
location / {
    proxy_pass http://127.0.0.1:8100;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;          # SSE 不能被缓冲
    proxy_read_timeout 300s;
}
```

## 备份

要备份的只有两样：**库**和**图片目录**。

```bash
sqlite3 /var/lib/scbot/scbot.db ".backup '/backup/scbot-$(date +%F).db'"
tar czf /backup/media-$(date +%F).tar.gz -C /var/lib/scbot media
```

不要直接 `cp` 正在写的库（还有 `-wal` 与 `-shm`），用 `.backup`，或者停服务再拷。

页面上的「设置 → 数据包」是另一条路：导出一份 JSON（对话、记忆、档案），换机器时导入。它不含图片。

## 换模型

三个 `CHAT_LLM_*` 换成别的 OpenAI 兼容服务即可，`systemctl restart scbot-new`。想让某一轮用你自己的 key，在「设置 → 自带密钥」里填，它会随这一轮发出去、**不写入库、也不计入每小时限额**。

判官和记忆抽取默认跟对话模型同一个；想省钱可以单独指一个便宜的小模型：

```
SCCORE_JUDGE_MODEL=某个小模型
```

**注意**：自带密钥只替换对话这一路的 key；判官与记忆抽取仍用部署的 key。

判官想用别的服务（上游的 JEV 走 OpenRouter，就是这样），再加地址与 key：

```
SCCORE_JUDGE_BASE_URL=https://openrouter.ai/api/v1
SCCORE_JUDGE_API_KEY=…
SCCORE_JUDGE_MODEL=…
```

对话仍在 `CHAT_LLM_*`，只有判断那一半换地方。

想完全不出网，就 `SCCORE_FAKE=1`：回复是固定台词，判官与记忆都不会真的运行，界面与流程完整。

## 角色的资料：数据版还是向量版

`characters/facts/<id>.md` 是这位角色的资料（喜好、经历、人际关系；上游每人约 8KB，和 `sheets/` 里那份人设分开写）。同一份资料有三种用法，改 `.env` 后重启即切换，不动代码也不用重导数据：

| `SCCORE_RECALL` | 是什么 | 需要什么 | 代价 |
|---|---|---|---|
| `off`（默认） | 只有人设，资料不参与 | — | 零 |
| `whole` | **数据版**：整份资料跟人设一起进提示词，一起被服务商缓存 | — | 第一段提示词长一份资料（约 8KB），之后通常只按缓存价算 |
| `vector` | **向量版**：资料按标题切段、嵌入；每轮只带最相关的 `SCCORE_RECALL_TOP` 段 | 一个 OpenAI 兼容的 `/embeddings` | 首次把每段各嵌入一次；之后每轮多一次查询嵌入（几十毫秒） |

数据版改向量版（硅基流动的 Qwen3-Embedding 是最省事的一家，OpenAI、Jina、本地 Ollama / LM Studio 同样的形状）：

```
SCCORE_RECALL=vector
SCCORE_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
SCCORE_EMBEDDING_API_KEY=…
SCCORE_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

`SCCORE_RECALL_TOP`（默认 4）是每轮带几段，`SCCORE_RECALL_MIN`（默认 0.35）是余弦下限：她引自己的资料太多就调大，什么都不提就调小。这两个都要按你选的嵌入模型试一次。

再往前一步是**门控**：每轮先让判断模型答一句「这一句要不要查资料」，闲聊就不查、也就没有查询嵌入的延迟。上游用 JEV（`typesafe/jev-1.13`，经 OpenRouter）；这里任何 OpenAI 兼容的判断模型都行，问题加在判断模型本来就有的那次请求里，不额外多一次调用：

```
SCCORE_RECALL_GATE=judge
```

**JEV 与嵌入是两件事**，容易混：向量化用的是**嵌入模型**（`SCCORE_EMBEDDING_MODEL`，默认 `Qwen/Qwen3-Embedding-0.6B`）；JEV 是**判断模型**，它只看一眼这句话、回答「要不要查」，不产生向量。上游把两者分开，这里也一样——可以只用嵌入不开门控，也可以用便宜的小模型做门控而不碰 JEV。

**失败时**：嵌入服务不通、没有一段的余弦达到 `SCCORE_RECALL_MIN`、判断模型没答出来——这一轮就不带资料，话照说；判断模型失败时反而**照查**（跟上游一样，宁可多查一次也不漏）。嵌入的成败记在服务状态页的「检索」一项。

**看得见的状态**：`GET /api/recall?c=mano` 给出这份资料切了几段、已经嵌入几段、用的是哪个模型。第一次向量版对话会先把这份资料嵌完（几十段，一次几十毫秒）。

**换模型或维度**：向量按「角色 + 模型 + 原文」存，换了模型就等于换了一整套，下一次对话会重新嵌入；资料文件改了只重嵌改动的那几段，不用重启，也不用清库。

**没有剧情库**：上游还会把 47MB 的剧情台词向量化，并让模型自己调用 `recall_story`。剧情数据没有许可证、不适合随仓库发，所以这里只做角色资料，不做剧情库与重排。

## 升级

```bash
cd /opt/scbot-new
sudo -u scbot git pull            # 或解压新的 tar 覆盖
sudo systemctl restart scbot-new
```

库结构变化由插件自己的 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN` 处理，启动时执行；旧的表会被就地补上缺的列，数据不用导出导入。

升级前备份一次库，是唯一值得养成的习惯。

## 换个端口 / 只听本机

```
SCCORE_HOST=127.0.0.1
SCCORE_PORT=8200
```

只听本机时，反向代理或 SSH 端口转发是唯一的入口。

## 常见问题

**页面能开，但没有回复。**
看「设置 → 关于 → 服务状态」：`chat_llm` 是「不稳」或「停」，就是模型这一路的问题（key、地址、模型名、余额）。`journalctl -u scbot-new` 里只有访问日志，模型错误在页面上。

**一上来就问「礼物暂时没有开放」。**
`characters/gifts.toml` 没读到或写错了（少字段、每档没有一件没下架的礼物）。启动日志里会写原因；修好重启即可。

**时间是 UTC，热力图对不上。**
统计按「制作人所在时区」算：设置 → 档案里的时区，其次是浏览器报的。两个都没给，就是机器的时区。

**记忆一直不增长。**
记忆是**后台**写的，写完要几秒；设置页会等最多 5 秒。如果模型总也不按 JSON 输出，就一条都不会有——换一个听得懂「只输出 JSON」的模型。
