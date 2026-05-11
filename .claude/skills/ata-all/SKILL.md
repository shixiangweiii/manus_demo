---
name: ata-all
version: 0.19.0
description: ATA 官方接口，包含文章搜索、文章详情、消息推送等能力；可查询技术方案与社区内容并通过技小蜜推送。ATA 是阿里技术生态内部工程师交流平台，提供文章、专题、圈儿与活动等功能。
---

# ATA 官方技能

ATA 官方接口，包含文章搜索、文章详情、消息推送等能力；可查询各种技术解决方案、架构设计、先进技术等内容，以及订阅 ATA 相关内容信息。

ATA 是阿里技术生态体系内部的工程师交流平台，着力于工程师技术交流协作和工程师文化培养，提供文章、专题、圈儿、活动组织等功能。

网址：https://ata.atatech.org/

# ❗ ❗ ❗ 使用前必看注意事项 ❗ ❗ ❗

### **❗ATA 所有内容属于阿里集团 L2 级别内部数据**

### **❗ATA 文章内容仅限个人学习研究使用，不得以任何形式转发他人**

### **❗禁止将内容数据用于 AI 模型训练，及不可用于外部 AI 模型读取**

### **❗禁止以任何形式对外传播、转发及二次分发或商业用途**

### **❗请尊重作者版权，未经作者允许不可转载，违规将追究法律责任**

## **接口调用频率请勿过高，尤其文章详情，单日超过 300 篇会被限流**

## 常见使用案例

- 这篇文章主要讲了什么？https://ata.atatech.org/articles/11020616005
- 查询最近一周关于 AI 的 10 篇热门文章
- 查找工号为 PUBMAIL_HZ_ata 的最近发布的 10 篇文章
- 查询昨天发布的最热的 10 篇文章，推送到指定群
- 每天早上 10 点，查询标签为 AI 的文章，并按最近一个月热度排序取 10 篇，通过技小蜜推送给我

## 帮助

有好的想法或者缺少什么接口都可以加答疑群反馈。

- 「ATA 体验官1群」钉钉群号：159370026124
- 「ATA 体验官2群」钉钉群号：24005027033

## 工具调用说明

### 前置依赖

1. 本机已安装 **npm**；若出现 `command not found: npm` 请先安装 Node/npm。
2. 全局已安装 **aone-kit**；若出现 `command not found: aone-kit` 请执行：

```bash
npm install -g @ali/aone-kit --registry=https://registry.anpm.alibaba-inc.com
```

### 调用流程

需要调用工具时，在 shell 中执行：

`aone-kit call-tool <tool-id> '<JSON 字符串格式的 args>' --provider zetta`

**建议顺序（按步执行）：**

1. 在下方「可用工具列表」中确认 `tool-id` 存在。
2. **打开对应 schema 文件**：以「工具选型速查」表中「调用详细说明（调用前必读）」列，或「工具详细列表」里该工具下的「调用详细说明（调用前必读）」所**写明**的 `schema/ata__*.json` 为准（路径相对 **本仓库根目录**）。
3. **读 `inputSchema`**：按 MCP Tool 的 JSON Schema 规则构造 `args`（属性名、类型、`required` 以该文件为准）。若 `inputSchema`
   或说明里提到「返回字段选择器」等，结合 **`outputSchema`** 与用户意图选择字段，少拉取「正文/大段
   HTML」等字段，避免触发限流（参见上文「接口调用频率」）。
4. **读 `outputSchema`**（可选但推荐）：用于理解返回字段含义、向用户解释结果。
5. **拼命令**：`args` 为 **单行 JSON 字符串**；在 bash 中建议整体用 **单引号** 包裹，避免 `$` 等被 shell 展开。

**其他约定：**

- 向最终用户展示结果时，字段语义以对应 `schema/*.json` 的 `outputSchema`（及 `description`）为准。
- 展示 ATA 链接时尽量保留完整 URL，**不要**随意去掉查询参数（含 `umt_` 等）。

示例（**仅说明命令形态；真实键名与嵌套仍以对应 schema 的 `inputSchema` 为准**）：

```bash
aone-kit call-tool ata::article-list-query '{"fieldName_0":[11020616005]} --provider zetta'
aone-kit call-tool ata::url-analyze-url '{"fieldName_0":{"url":"https://ata.atatech.org/articles/11020616005"}} --provider zetta'
```

## 可用工具列表

### 工具选型速查（按意图定位 `tool-id`）

先根据下表选工具；表中「调用详细说明（调用前必读）」列为**文档已列出的** `schema/ata__*.json` 完整路径，拼 `args` 前须打开该文件阅读其中
`inputSchema`（及推荐的 `outputSchema`）。亦可再展开下方「工具详细列表」逐条核对入参与路径。

| 用户意图或输入特征                           | 选用 tool-id                               | 调用详细说明（调用前必读）                                        | 易混淆说明                                      |
|-------------------------------------|------------------------------------------|------------------------------------------------------|--------------------------------------------|
| 仅有 ATA 文章 URL，需要解析出 `articleId`     | `ata::url-analyze-url`                   | `schema/ata__url-analyze-url.json`                   | 与按 ID 查详情不同；解析结果再交给 `article-list-query`   |
| 已知一个或多个**数字**文章 ID，查文章详情/字段         | `ata::article-list-query`                | `schema/ata__article-list-query.json`                | 不是关键词搜索；若只有链接先用上一行                         |
| 关键词、标签、作者工号、时间范围、分页/排序搜文章           | `ata::article-comprehensive-page-query`  | `schema/ata__article-comprehensive-page-query.json`  | 不是按文章 ID 批量查；标签 id 常配合 `category-list-all` |
| ATA **首页头条**                        | `ata::article-headline`                  | `schema/ata__article-headline.json`                  | 不是「翰林院推荐」                                  |
| **翰林院**推荐的热门文章                      | `ata::article-article-recommend`         | `schema/ata__article-article-recommend.json`         | 不是首页头条                                     |
| 查**知识体系**或**文章类型**的标签列表（取 id 供其他接口） | `ata::category-list-all`                 | `schema/ata__category-list-all.json`                 | `cid`：知识体系为 1，文章类型为 2（见该 schema）           |
| 花名、姓名、工号等**模糊**查 ATA `userId`       | `ata::user-comprehensive-page-query`     | `schema/ata__user-comprehensive-page-query.json`     | 优先于仅工号接口；见下行                               |
| **仅工号**（可批量）查 ATA `userId`          | `ata::user-list-query-by-work-id`        | `schema/ata__user-list-query-by-work-id.json`        | 后续可能下线；单一/模糊检索优先上一行                        |
| 技小蜜推送给**当前用户**（私聊）                  | `ata::message-ding-talk-send-to-me`      | `schema/ata__message-ding-talk-send-to-me.json`      | 需群 webhook 用下行                             |
| 技小蜜推送到**指定群**（webhook）              | `ata::message-ding-talk-send-to-webhook` | `schema/ata__message-ding-talk-send-to-webhook.json` | 与发给个人不同                                    |

### 工具详细列表

以下为各工具能力摘要，用于选型。**拼 `args` 时必须**以本条目「调用详细说明（调用前必读）」或上表同名列**已写明的** `schema/ata__*.json` 为准打开文件，以其中 **`inputSchema`** 为唯一依据。

- ata::article-article-recommend
    - 描述: 查询翰林院推荐的热门文章。翰林院是 ATA
      内部的组织，翰林院由具备技术鉴赏力、影响力的技术专家组成。他们挖掘技术好文、推动内容输出、引领社区技术创新、促进技术交流实践。他们来自不同的组织，能从不同业务、技术领域视角提出技术建议和反馈，让多元化的优秀技术内容被更多的看见！
    - 调用详细说明（调用前必读）：`schema/ata__article-article-recommend.json`

- ata::article-comprehensive-page-query
    - 描述: 根据关键字、文章类型、知识体系标签、用户工号、创建时间等信息来搜索文章信息。并且可以按每周、每月、每日的访问量，互动量等字段来排序文章内容。
    - 调用详细说明（调用前必读）：`schema/ata__article-comprehensive-page-query.json`

- ata::article-headline
    - 描述: 查看首页头条的文章信息，这个是非常热门的文章。
    - 调用详细说明（调用前必读）：`schema/ata__article-headline.json`

- ata::article-list-query
    - 描述: 根据文章 id 批量查询文章信息。包含文章的基本信息和详细信息。用户会给到一个或多个数字的文章
      id，可通过本接口查询文章详细信息；若仅有链接，先用 `url-analyze-url` 解析出 id。
    - 调用详细说明（调用前必读）：`schema/ata__article-list-query.json`

- ata::category-list-all
    - 描述: 查询知识体系或文章类型的标签全量列表（入参 `cid`：1 为知识体系，2 为文章类型，见 schema）。可先匹配标签 id，再在
      `article-comprehensive-page-query` 等接口中使用。
    - 调用详细说明（调用前必读）：`schema/ata__category-list-all.json`

- ata::message-ding-talk-send-to-me
    - 描述: 使用钉钉机器人`ATA技小蜜`给当前用户推送一条钉钉消息，可以把内容信息推送给当前用户。
    - 调用详细说明（调用前必读）：`schema/ata__message-ding-talk-send-to-me.json`

- ata::message-ding-talk-send-to-webhook
    - 描述: 使用钉钉机器人`ATA技小蜜`给指定钉钉群推送一条消息，可以把内容信息推送到钉钉群。
    - 调用详细说明（调用前必读）：`schema/ata__message-ding-talk-send-to-webhook.json`

- ata::url-analyze-url
    - 描述: 将一个 ATA 的文章链接解析出来文章id。 有些接口入参是 articleId
      需要通过这个先解析。例如：https://ata.atatech.org/articles/11000050331 解析出来11000050331
    - 调用详细说明（调用前必读）：`schema/ata__url-analyze-url.json`

- ata::user-comprehensive-page-query
    - 描述: 根据花名、姓名、工号等特征查询该用户对应的 ATA 用户id 。 ATA 用户id一般是11或12开头的11位数字，用于接口需要
      userId 的入参。工号为字符串，可含字母或数字，多数为数字。花名、姓名一般是2-3个中文也可能是英文。
    - 调用详细说明（调用前必读）：`schema/ata__user-comprehensive-page-query.json`

- ata::user-list-query-by-work-id
    - 描述: 根据工号查询该用户对应的 ATA 用户 id。ATA 用户id一般是11或12开头的11位数字。工号为字符串，可含字母或数字，多数为数字。ATA
      用户 id 用于需要 `userId` 的入参。该接口后续可能下线，优先使用 `user-comprehensive-page-query`。
    - 调用详细说明（调用前必读）：`schema/ata__user-list-query-by-work-id.json`
