# 第三方资源

这个仓库里除了自己的代码，还带着别人的东西。它们各自按自己的许可证分发，不是本项目的
财产；这一页是它们的归属声明。

## 字体

`web/assets/media/` 下 231 个 `.woff2` 是**子集化后的网页字体**（按页面用到的字切过，
所以文件名是哈希）。它们来自：

| 字体 | 用在哪 | 版权 | 许可证 |
|---|---|---|---|
| **Noto Sans SC** | 中文界面 | © 2014–2025 Adobe（https://github.com/notofonts/noto-cjk） | SIL Open Font License 1.1 |
| **LINE Seed JP** | 日文界面（`--font-ja`） | © LY Corporation | SIL Open Font License 1.1 |

两者都允许随任意软件打包、嵌入和再分发，条件是**保留版权声明和许可证**（就是这一页），
并且不能把字体文件本身单独出售。子集化不改变许可证。

如果你是重新分发这份代码，请保留这个文件。如果你换了字体，把这一节改掉。

## 只在前端构建时用到

- **Tailwind CSS** —— MIT。编译后的样式在 `web/assets/css/`，仓库里不含它的源码。

## 代码里没有的

- 角色设定、立绘、语音、剧情文本：不在这个仓库里，见 `characters/README.md`。
- 任何个人数据（QQ 号、群号、聊天记录、数据库）：不在这个仓库里，见 `.gitignore`。

---

## SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007

PREAMBLE

The goals of the Open Font License (OFL) are to stimulate worldwide development of
collaborative font projects, to support the font creation efforts of academic and linguistic
communities, and to provide a free and open framework in which fonts may be shared and
improved in partnership with others.

The OFL allows the licensed fonts to be used, studied, modified and redistributed freely as
long as they are not sold by themselves. The fonts, including any derivative works, can be
bundled, embedded, redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives, however, cannot be
released under any other type of license. The requirement for fonts to remain under this
license does not apply to any document created using the fonts or their derivatives.

DEFINITIONS

"Font Software" refers to the set of files released by the Copyright Holder(s) under this
license and clearly marked as such. This may include source files, build scripts and
documentation.

"Reserved Font Name" refers to any names specified as such after the copyright statement(s).

"Original Version" refers to the collection of Font Software components as distributed by the
Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting, or substituting --
in part or in whole -- any of the components of the Original Version, by changing formats or
by porting the Font Software to a new environment.

"Author" refers to any designer, engineer, programmer, technical writer or other person who
contributed to the Font Software.

PERMISSION & CONDITIONS

Permission is hereby granted, free of charge, to any person obtaining a copy of the Font
Software, to use, study, copy, merge, embed, modify, redistribute, and sell modified and
unmodified copies of the Font Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components, in Original or Modified
Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled, redistributed and/or
sold with any software, provided that each copy contains the above copyright notice and this
license. These can be included either as stand-alone text files, human-readable headers or in
the appropriate machine-readable metadata fields within text or binary files as long as those
fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font Name(s) unless explicit
written permission is granted by the corresponding Copyright Holder. This restriction only
applies to the primary font name as presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font Software shall not be
used to promote, endorse or advertise any Modified Version, except to acknowledge the
contribution(s) of the Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole, must be distributed
entirely under this license, and must not be distributed under any other license. The
requirement for fonts to remain under this license does not apply to any document created
using the Font Software.

TERMINATION

This license becomes null and void if any of the above conditions are not met.

DISCLAIMER

THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL
THE COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, INCLUDING ANY
GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF THE USE OR INABILITY TO USE THE FONT
SOFTWARE OR FROM OTHER DEALINGS IN THE FONT SOFTWARE.
