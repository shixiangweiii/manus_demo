# ATA 有哪些基础的接口

目前支持开放的接口都放在 aone 的 mcp 市场上面，可以自己去查询：https://open.aone.alibaba-inc.com/mcp/server/ata

# 如何开发一个 ATA skill

* 看下 mcp 的基础接口知道 ATA 包含哪些基础技能
* 在 https://open.aone.alibaba-inc.com/market 右上角可以点击创建skill
* 创建流程参考官方文档：https://alidocs.dingtalk.com/i/nodes/KGZLxjv9VGkoG9YwHY92oEkRV6EDybno
* 额外注意点
    * 需要 参考 https://code.alibaba-inc.com/aone-open-skill/ata-all 也就是当前工程
    * 必须注意需要把下面2个`------` 之间的内容复制到你的skill的最后，不然你的技能会传参数失败导致无法调用接口。这个和aone也在沟通中看看后续有没有优化方案。

------

## aone-kit 工具调用注意点

* `参数列表` 中的`fieldName_0`、`fieldName_1` 也需要放到调用参数中的
* 重点关注下 args 中请求的 json 字符串的一级字段都是 `fieldName_0`、`fieldName_1`之类的
* 正确案例 ，看到 `参数列表` 包含了 `fieldName_0` ，则请求 json 参数也需要包含 `fieldName_0`：

```
aone-kit call-tool ata::url-analyze-url '{"fieldName_0":{"url": "https://ata.atatech.org/articles/11020428816"}}' --provider zetta
```

------

## 帮助

有好的想法或者缺少什么接口都可以加答疑群反馈

* “ATA 体验官1群--问题反馈群”群的钉钉群号： 159370026124
* “ATA体验官2群-咨询反馈”群的钉钉群号： 24005027033
