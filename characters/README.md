# characters/ —— 角色层

这个仓库**不附带任何角色**。这里只有格式、示例和骨架，内容由部署者自己准备。

原因有两个，都不是技术上的：角色设定、立绘、剧情台词是别人的作品，原作方和配音演员的
权益在里面；而一份写着某个具体角色的人设，也注定只对一个部署者有意义。

## 最少要准备什么

```
cp characters/characters.example.toml characters/characters.toml
cp characters/units.example.toml      characters/units.toml     # 可选
mkdir -p characters/sheets characters/avatars characters/world
```

然后写 `characters.toml` 里那几条，`sheet` 指向的人设文件放 `characters/sheets/<id>.md`。
没有 `characters.toml` 时，目录里每个 `*.md` 都会被当成一个角色（文件名就是 id）——
够跑起来，但没有颜色、没有简介。

## 目录里各管什么

| 位置 | 必需 | 作用 |
|---|---|---|
| `characters.toml` | **是** | 角色表：id、名字、颜色、人设路径，以及恋爱系统的三个数 |
| `units.toml` | 否 | 组合表，只影响对话列表的分组与搜索候选 |
| `sheets/<id>.md` | 是 | 人设正文。会进提示词的 persona 槽，按角色缓存 |
| `avatars/<id>.jpg` | 否 | 头像。没有就是没有，不报错 |
| `world/<id>-*.md` `world/<id>-*.toml` | 否 | 群聊/私聊的语气、兴趣、话题、拒绝话术等，见 `world/README.md` |
| `facts/<id>.md` | 否 | 大段背景资料。`SCCORE_RECALL=whole` 时整份进提示词 |
| `facts/*/` | 否 | 可检索的剧情片段，按 `world/<id>-stories.toml` 的话题命中才注入 |
| `gifts.toml` | 否 | 礼物目录 |
| `memory/` | 自动生成 | 她自己的记忆文件，**不要提交、不要分享** |

## 三个和恋爱有关的数

只有 QQ 私聊用得到，网页端不读。

- **`adult`**：**学段，不是民法成年**。高中及以上写 `true`，初中・小学写 `false`。
  15 岁可能是初三也可能是高一，光看 `age` 分不出来，所以一个个填。
  **不写等于没人填过，按未成年处理**——漏判一个未成年的代价比漏判一个大。
- **`plove`** 0–5：她会不会往那个方向走。`0` 不是「很难」，是「没有这条线」——
  永远不进入恋爱，连暧昧都不给。
- **`heavy`** 1–5：「重」的程度，**只有强度，没有方向**。5 可能是照顾得无微不至，
  也可能是两小时没回就发十条，那由人设决定，不由这个数决定。

这三个数的判据只能来自原作（剧情、官方设定），不能拿「部署者和她聊了多少」倒推——
那样同一个角色换个部署者就是另一个数，这个字段也就没有意义了。

## 不要放进来

- 任何官方立绘、语音、剧情文本、歌曲信息；
- 你自己的 `memory/`、数据库、`.env`；
- 别人的 QQ 号、群号。

`.gitignore` 已经挡了上面这些，但它只是最后一道门——写之前先想一下这份文件该不该公开。
