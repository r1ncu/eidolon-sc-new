# 版权与来源

## 本仓库

`scbot-new` 是 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc) 的衍生作品，按上游的许可以 **GNU AGPL-3.0** 发布，全文见 `LICENSE`。

修改与重写的部分（相对上游）：

- `core/`：把上游一体的后端拆成「只认识回合的内核」——协议解析、提示词四槽、存储、回合、插件宿主、判官总线、OpenAI 兼容客户端。全部重写，语义照抄。
- `plugins/`：插件化的玩法（角色、限额、发图、好感度、记忆、礼物、档案、服务状态、语音）。
- `web/`：手写单页前端，DOM 与 class 逐页照抄上游，脚本与模板重写；CSS 用的是上游编译出来的产物。
- `docs/`、`deploy/`：新增。

数值、提示词文本、界面文案（`web/assets/app.js` 的 `T`）、角色配置格式与角色人设来自上游仓库，版权归原作者。

## 上游的额外许可（AGPL-3.0 第 7 条）

> If you modify this Program, or any covered work, by linking or combining it with the Spine Runtimes of Esoteric Software (or a modified version of that library), containing parts covered by the terms of the Spine Runtimes License, the licensors of this Program grant you additional permission to convey the resulting work.

本仓库**不包含** Spine 运行库（`frontend/public/vendor/spine-3.6/`），也没有用到立绘。

## 角色与素材

`characters/` 里的人设、头像（`avatars/`、`avatars-scsp/`）与 `web/assets/gifts/` 的礼物插画来自上游仓库，属于《偶像大师 闪耀色彩》（THE IDOLM@STER SHINY COLORS）及其权利方。

本仓库是**非商业的粉丝作品**，与 Bandai Namco Entertainment、Bandai Namco Studios 无任何关系，也未获其授权或背书。如果你是权利方并认为这里有不当使用，请开 issue，我会移除相关素材。

## 第三方代码

无。运行时只用 Python 标准库；`web/assets/css/app.css` 是上游用 Tailwind CSS 编译出来的样式产物，`web/assets/icons.js` 是从上游所用的图标库中取出的 SVG 路径数据。
