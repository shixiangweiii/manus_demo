---
name: build-ata-all
description: 从 a1 安装的 ata-all 技能包同步「可用工具列表」到仓库根目录 schema JSON 与 SKILL.md，并按 ata-domain-client v2 接口补充 javaMethod 与 outputSchema。在维护 ata-all 仓库、更新 MCP 工具定义或对齐 Java 客户端返回类型时使用。
---

# build-ata-all

在 **ata-all 仓库根目录** 执行，将 `a1 skill install` 产出的 `SKILL.md` 中的工具列表落地为 `schema/` 下的 JSON，并与根目录 `SKILL.md`、Java 客户端方法签名保持一致。

## 前置条件

- 已安装可执行 `a1`（以及安装技能所需的网络/权限）。
- 本机存在 **ata-super** 仓库路径（默认）：`/Users/zhuangjiaju/IdeaProjects/ata-super/ata-domain/ata-domain-client/src/main/java/com/alibaba/atasuper/client/v2`。若路径不同，在操作中替换为实际路径。

## 步骤 1：安装技能包到 target

在项目根目录执行：

```bash
a1 skill install ata-all-test --global --location ./target/
```

安装完成后，权威工具列表以 **`./target/ata-all/SKILL.md`** 为准（尤其是「可用工具列表」一节）。

## 步骤 2：解析「可用工具列表」

1. 打开 `./target/ata-all/SKILL.md`，定位标题 **`## 可用工具列表`**（或等价标题）。
2. 其后每条工具通常形如：
   - 一行：`- ata::<tool-local-id>`（或带命名空间前缀的完整 tool-id）
   - 下一行起：`- 描述: ...`（可多行）
3. 记录每个工具的 **`id`**（完整 `ata::...`）与 **描述** 文本，用于生成/更新 JSON 与根目录 `SKILL.md`。

## 步骤 3：写入 `schema/` 目录（冲突则覆盖）

1. **文件名规则**：将 tool-id 中的 `::` 替换为 `__`，再加后缀 `.json`。  
   例：`ata::article-list-query` → `schema/ata__article-list-query.json`。
2. 每个文件为 **一个工具一条 JSON**，建议包含字段：
   - `version`：数字，与仓库惯例一致即可（如 `1`）。
   - `id`：完整 tool-id（如 `ata::article-list-query`）。
   - `description`：与安装包中该工具描述一致。
   - `javaMethod`：见下文「Java 映射」；在 `com.alibaba.atasuper.client.v2` 下定位 **接口名 `#方法名`**。
   - `inputSchema`：**JSON Schema 形态**，遵循 MCP **Tool `inputSchema`** 惯例（`type`、`properties`、`required`、`description` 等），与 aone-kit 调用 `args` 一致。
   - `outputSchema`：与 `inputSchema` 同一套 JSON Schema 写法，描述 **Java 方法返回类型** 的结构化形状（见步骤 5）。
3. **若 `schema/` 中已存在同名文件**：用本次生成结果 **整文件替换**（用户要求冲突则覆盖）。

## 步骤 4：同步根目录 `SKILL.md` 中「可用工具列表」

编辑仓库根目录 **`SKILL.md`**（路径：`/Users/zhuangjiaju/IdeaProjects/ata-all/SKILL.md`）：

1. 保持「调用流程」「文件名规则」等说明与 **步骤 3** 一致：强调拼 `args` 时读 `schema/` 下对应 JSON 的 `inputSchema`；文件名规则 `::` → `__`。
2. 「**可用工具列表**」小节下的条目：**tool-id 与描述** 与 `./target/ata-all/SKILL.md` **一致**（可与步骤 2 解析结果逐条对照）。
3. 若根 `SKILL.md` 需说明输出结构：写明完整 **`outputSchema`** 见同名的 `schema/*.json` 中 `outputSchema` 字段。

## 步骤 5：`javaMethod` 与 `outputSchema`

### 5.1 在 v2 包中匹配接口与方法

- **规则**：`ata::<segment>-<rest...>` 中 **去掉 `ata::` 后的 kebab-case** 对应 Java 侧「领域 + 方法语义」。在 `client/v2` 下按 **包名 + `*ClientService` 接口** 查找 **camelCase 方法名**。
- **示例**（当前 ata-all 常用工具与接口对应关系，供核对；新增工具时在本包内按同样方式查找）：

| tool-id（`ata::` 后） | javaMethod |
|----------------------|------------|
| `article-article-recommend` | `com.alibaba.atasuper.client.v2.article.ArticleClientService#articleRecommend` |
| `article-comprehensive-page-query` | `com.alibaba.atasuper.client.v2.article.ArticleClientService#comprehensivePageQuery` |
| `article-headline` | `com.alibaba.atasuper.client.v2.article.ArticleClientService#headline` |
| `article-list-query` | `com.alibaba.atasuper.client.v2.article.ArticleClientService#listQuery` |
| `category-list-all` | `com.alibaba.atasuper.client.v2.category.AtaCategoryContentClientService#listAllFromCache` |
| `message-ding-talk-send-to-me` | `com.alibaba.atasuper.client.v2.message.MessageClientService#dingTalkSendToMe` |
| `message-ding-talk-send-to-webhook` | `com.alibaba.atasuper.client.v2.message.MessageClientService#dingTalkSendToWebhook` |
| `url-analyze-url` | `com.alibaba.atasuper.client.v2.url.UrlClientService#analyzeUrl` |
| `user-comprehensive-page-query` | `com.alibaba.atasuper.client.v2.user.DomainUserClientService#comprehensivePageQuery` |
| `user-list-query-by-work-id` | `com.alibaba.atasuper.client.v2.user.DomainUserClientService#listQueryByWorkId` |

- 若同一语义在多个重载间选择：以 **与 MCP 入参形态一致** 的那一版为准（例如 URL 解析单条用 `analyzeUrl`，非 `analyzeUrlBatch`）。

### 5.2 从返回类型推导 `outputSchema`

1. 打开上表对应接口源码，查看方法 **返回类型**（如 `AtaListResult<ArticleClientDTO>`、`AtaPageResult<...>`、`AtaPojoResult<...>`、`AtaActionResult`）。
2. 在 `outputSchema` 中按 MCP 风格描述：
   - 包装类的公共字段：`success`、`errorCode`、`errorMsg`、`traceId` 等（以 **ata-super 中实际 Result 类型** 为准）。
   - `content` / 分页字段等：与泛型 DTO 对齐；复杂 DTO 可用 `properties` 展开，或使用 `$ref` 指向同文件 `$defs`（与现有 `schema/ata__article-article-recommend.json` 等保持一致）。
3. **类型/字段以 Java DTO 与 Result 定义为准**，避免凭空虚造；必要时读取 `ArticleClientDTO`、`AtaPageResult` 等源文件补全字段。

## 步骤 6：自检清单

- [ ] `./target/ata-all/SKILL.md` 中列出的每个工具在 `schema/` 中均有对应 `ata__*.json`。
- [ ] 根目录 `SKILL.md`「可用工具列表」与安装包一致，且与 `schema` 文件名规则描述一致。
- [ ] 每个 JSON 含 `inputSchema` 与 `outputSchema`，且 `javaMethod` 可在 v2 包中定位到唯一方法。

## 补充说明

- **inputSchema** 若需从别处生成，仍以 MCP 对 tool 的 inputSchema 约定为准；与 `aone-kit call-tool <tool-id> '<json args>'` 的 `args` 结构一致。
- 本技能不强制实现自动化脚本；若用户希望固化流程，可在本目录下增加 `scripts/` 并由技能引用（见 Cursor create-skill 惯例）。
