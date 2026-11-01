# characters/ —— 角色层

这个仓库**不附带任何角色**。这里只有格式、示例和骨架，内容由部署者自己准备。

原因有两个，都不是技术上的：角色设定、立绘、剧情台词是别人的作品，原作方和配音演员的
权益在里面；而一份写着某个具体角色的人设，也注定只对一个部署者有意义。

## 最少要准备什么

```
cp characters/characters.example.toml characters/characters.toml
cp characters/units.example.toml      characters/units.toml     # 可选
mkdir -p characters/sheets characters/avatars characters/world
mkdir -p characters/works && cp characters/work.example.toml characters/works/<作品id>.toml  # 可选
```

然后写 `characters.toml` 里那几条，`sheet` 指向的人设文件放 `characters/sheets/<id>.md`。
没有 `characters.toml` 时，目录里每个 `*.md` 都会被当成一个角色（文件名就是 id）——
够跑起来，但没有颜色、没有简介。

## 目录里各管什么

| 位置 | 必需 | 作用 |
|---|---|---|
| `characters.toml` | **是** | 角色表：id、名字、颜色、人设路径，以及恋爱系统的三个数 |
| `works/<id>.toml` | 否 | 作品表：标题、事务所、话题词。一个作品一份，**平级**；角色用它区分归属 |
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

## 两条线：世界是共享的，视角是她的

改一个人不再需要改代码。文件分两层，各有各的归属：

```
characters/
  works/<id>.toml           # 作品：标题、事务所、聊到作品本身时的关键词。一个作品一份，彼此平级
  work.toml                 # 单作品简写：`works/` 为空时才读，不是「主作品」
  world/
    work.md                 # 作品的客观事实（共享；没有任何第一人称）
    office.md               # 名单：谁、在哪个组合、一句话（共享）
    idols/<id>.md           # 某位同行的详细资料（共享，提到名字才注入）
  packs/<id>/               # 一个角色一个包，可以整包增删
    persona.md              # 人设（旧位置 sheets/<id>.md 仍然认）
    facts.md                # 角色资料（旧位置 facts/<id>.md 仍然认）
    view.md                 # **她怎么叫这些人、谁在她心里什么位置**（没有共享版本）
    world.md                # **她怎么看这个作品**（没有共享版本）
    names.toml              # 自称、怎么叫对面、哪些名字算她（旧 stage.toml 里那段）
    voice/qq.md dm.md stage.toml refusals.toml interests.md qzone.md
    voice/tease.toml        # 她自己的特别设定：机制要的数据（记号、额度、那一句）
    voice/tease.md          # 同一件事的人设段落，语域里的 {{extra}} 是它的位置
    relations.toml stories.toml facts-stories/ assets/ memory/
    facts-live.md           # 资料：现实公演的年表（给人读的提炼稿，不注入）
    facts-live/             # 资料：那份年表的来源 `sc-events.csv`
```

- `facts-live` 是**语料**不是设定：`stories.toml` 里「现实企划的演出」那几段是从它提炼的，
  提示词用的是 `facts-stories/05-演出与活动/`，这两份只在读文件时给人看。

- 前两层是「世界」，最后那一层是「她」。**两条线成对注入**：只有事实，她是在念维基；
  只有视角，她没有可以站的地方。
- 解析顺序：先 `packs/<id>/…`，再旧的扁平名（`world/<id>-qq.md` 等）。所以可以把一个人
  慢慢搬过去，中间不会坏。搬完以后 `pack.summary()` 里 `fallback` 是空的——那是「一个人的
  东西全在自己包里」的判据。
- 启动那一行会告诉你读了什么：

  ```
  角色包 fuyuko / 在 packs/fuyuko / 旧路径：qq、dm、stage / 未写：background
  ```

  文件缺了不会报错（有中性退路），这一行是让「用错文件」看得见的地方。
- `/我说过`（私聊）用的是同一条路：角色的名字、卡名的署名，都从包里来。

## SCSP 素材：头像与聊天背景

官方 SCSP 那套图有两个用途，各自的开关都在「设置 → 外观」：

| 文件 | 管什么 | 开关 |
|---|---|---|
| `avatars/<id>.jpg` | 头像（列表、气泡旁边） | SCSP 头像 |
| `avatars-scsp/<id>.jpg` | SCSP 头像的另一版 | SCSP 头像（打开后用这一版） |
| `backgrounds/<id>.jpg` | 对话页的壁纸（`SfpChainProfileBg_<Name>.png` 那套） | SCSP 聊天背景 |

- 三个都在 `characters.toml` 里有对应的一行（`avatar` / `scsp_avatar` / `background`），
  按角色取，缺哪个就是没有那一样，页面自己退成印象色。
- 图片放进角色包也认：`packs/<id>/assets/{avatar,scsp,background}.*`，包里的优先。
- 后缀不用管：`.jpg` / `.png` / `.jpeg` / `.webp` 都认，条目里写着的那个优先——官方给的
  背景是 PNG，写 `backgrounds/<id>.jpg` 也不会因为后缀对不上就说「没有」。
- 壁纸是**铺在对话页最底下**的，顶部压了一层她的印象色，标题才看得清；气泡本身不透明，
  所以不挡字。

## 日历：她什么时候睡

```
characters/calendar.toml      # 日本的祝日表 + 这个部署的时区
```

- **看明天，不看今天**：明天要上班 → 23:00 睡；明天不上班（周末、祝日、`QQ_HOLIDAYS`）
  → 23:00 掷一次骰子，掷到 0–4 小时就熬几个小时，最晚到凌晨 3 点。所以周五、周六晚上熬，
  周日晚上不熬（周一要上班）。9:00 醒，两种晚上一样。
- 骰子一夜一次、存进 `qq_flags`（`sleep:<上床那天的日期>`），整夜不变——每轮重掷的话，
  她会在 0:30 醒、0:40 又睡。
- 「休息日」= 周末 + 这张表里的祝日。表由日本内阁府的公开 CSV 生成，含振替休日与
  「国民の休日」，目前覆盖 2026–2027。**每年更新一次**；没有的年份退成「只看周末」。
- `.env` 的 `QQ_HOLIDAYS` / `QQ_WORKDAYS` 仍然有效，优先于这张表（调休上班的周六、
  公司自己放的假）。
- 时区是 `SCCORE_TIMEZONE`，这个部署是 `Asia/Tokyo`。作息、祝日、记忆文件的日期都在
  这口钟上——北京时间和东京时间差一小时，别拿自己的表去对。
- 例外：好感度的**一天**边界（每日 +6 / +10 的额度）仍在 `affinity.DAY_TIMEZONE`
  （北京零点），是部署者定的，不要跟着改。
