# Vendored 前端依赖 / Vendored frontend dependencies

| 文件 File | 来源 Source | 版本 Version |
|---|---|---|
| `preact-htm-standalone.mjs` | npm 包 `htm` 的 `preact/standalone.module.js`（bundle 了 preact + hooks + htm，单文件零依赖） | htm 3.1.1 |

下载方式（国内镜像）/ Downloaded from China-friendly npm mirror:

```bash
curl -sL "https://registry.npmmirror.com/htm/3.1.1/files/preact/standalone.module.js" \
  -o preact-htm-standalone.mjs
```

导出 / Exports: `h, html, render, Component, createContext, useState, useReducer,
useEffect, useLayoutEffect, useRef, useImperativeHandle, useMemo, useCallback,
useContext, useDebugValue, useErrorBoundary`
