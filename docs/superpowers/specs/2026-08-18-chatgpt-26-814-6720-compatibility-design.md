# ChatGPT 26.814.41407（build 6720）兼容设计

## 背景

Codex Subscription Router 当前维护分支只验证到 ChatGPT
`26.810.52044`（build `6662`）。本机官方 ChatGPT 为
`26.814.41407`（build `6720`），`app.asar` SHA-256 为
`8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0`。
现有补丁器会在版本、build 或 ASAR 哈希不匹配时正确停止。

本任务在 workspace 内的维护 fork 上增加 build `6720` 的精确兼容，
使用 ad-hoc 签名生成独立应用，不修改官方 `/Applications/ChatGPT.app`。

## 目标

- 将 build `6720` 作为一个独立、精确识别的兼容目标。
- 为该 build 校准 ASAR、renderer、bootstrap 和 Computer Use 布局锚点。
- 保留所有替换数量和二进制常量检查；未知构建继续关闭失败。
- 通过 ad-hoc 签名安装 `~/Applications/Codex Subscription Router.app`。
- 验证应用签名、真实启动、loopback 健康状态及官方 ASAR 未改变。

## 非目标

- 不修改、降级或替换官方 ChatGPT 应用。
- 不放宽 `--allow-untested-source` 的默认保护。
- 不承诺 ad-hoc 签名下的 Appshots、Computer Use 或既有 macOS 隐私授权可用。
- 不在本任务中添加第二个订阅、执行 OAuth 登录或完成多账号路由验收。
- 不重构与 build `6720` 兼容无关的 mux、UI 或安装架构。

## 源码基线与工作位置

- 仓库：`/Users/wangshilian/gWorkspace/codex-subscription-router`
- 远端：`https://github.com/braindead-dev/codex-subscription-router.git`
- 基线提交：`8e94e9454793a2483eeeb35f45ce2ead87de4d8c`
- 本地分支：`codex/compat-chatgpt-26-814-6720`

Home 目录中的旧源码副本不作为输入。账号状态目录 `~/.codex-mux` 不在
源码清理范围内。

## 方案选择

采用维护 fork 直接增加 build `6720` 兼容。该基线已经包含 build `6662`
的双 Computer Use helper 布局、受限 entitlement 清理和后续路由修复。

不采用以下方案：

- 在归档上游上重新回移多个已关闭 PR：重复改动多，容易遗漏签名修复。
- 对现有补丁器直接传 `--allow-untested-source` 后安装：锚点匹配不等于语义
  正确，已知可能产生无法启动或持续占用 CPU 的应用。

## 兼容实现

### 构建识别

在 `TESTED_SOURCE_BUILDS` 中增加 build `6720` 的完整版本、build 和 ASAR
哈希三元组。最终安装不得使用 `--allow-untested-source`。

### ASAR 与 renderer

将官方 `app.asar` 解包到任务临时目录，对现有 build `6662` 的每个补丁点
逐一定位 build `6720` 对应实现。只有在确认组件语义相同后，才为新 build
增加专属 bundle 名、函数锚点、minified identifier 和预期替换数量。

build 分支应使用显式的版本/build 选择，不通过“某段文本碰巧存在”推断
目标版本。旧 build 的锚点和行为保持不变。

### Computer Use 与签名

核对 build `6720` 中两份内嵌 Computer Use service 的路径、bundle identifier、
团队常量及 ASAR 引用数量。每项都使用 build 专属期望值，数量不符立即失败。

本机没有 Apple Development 或 Developer ID 身份，因此最终构建使用
`--allow-adhoc-signing`。维护 fork 已移除不适用于独立签名副本的受限
entitlement 和上游 provisioning profile。最终仍需对主应用、内嵌 service
以及安装到 `~/Applications` 的独立 Computer Use helper 执行
`codesign --verify --deep --strict`。

## 数据流与安装边界

1. 补丁器只读取 `/Applications/ChatGPT.app` 作为构建输入。
2. ASAR 解包、二进制改写、打包和签名均在任务临时目录完成。
3. 所有兼容检查和签名验证通过后，才把独立应用原子安装到
   `~/Applications`。
4. 安装前后重新计算官方 `app.asar` 哈希，必须完全一致。
5. 如果任一步失败，不替换正式目标；任务创建的临时产物可安全清理。

## 错误处理

- 未识别版本、build、ASAR 哈希、bundle 布局或替换数量：立即失败。
- 某个锚点虽然匹配但无法证明语义对应：停止适配，不以计数通过代替审查。
- ad-hoc 签名校验失败或应用无法启动：安装判定失败，不保留半安装结果。
- Appshots 或 Computer Use 因 ad-hoc 权限受限：记录为已知限制，不影响核心
  Router 应用安装结论，但不得报告这些能力已验证。
- 需要用户完成 macOS 权限或订阅登录时，停在明确交互点，不代填凭据。

## 测试与验收

### 失败基线

- 记录未修改补丁器对 build `6720` 的兼容门禁失败。
- 对新增 build 选择和期望值先增加能暴露缺失兼容映射的测试或检查。

### 自动检查

- `npm ci --ignore-scripts`
- `npm run check`
- `npm run release:check`
- 对 build `6720` 执行完整补丁构建，且不使用 `--allow-untested-source`

### 本机烟雾验收

- 主应用、内嵌 Computer Use service 及独立 Computer Use helper 的
  `codesign --verify --deep --strict` 均通过。
- 启动独立 Router 应用并确认进程保持运行。
- loopback 健康端点返回成功。
- 应用窗口完成启动，不停留在无限 splash 或持续异常占用 CPU。
- 官方 `app.asar` 安装前后哈希均为
  `8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0`。
- 如未配置第二订阅，明确把多账号 OAuth 和实际跨账号路由列为未执行，
  不将其混入安装完成声明。

## 回滚与清理

补丁器只在所有检查通过后安装目标。若新应用已生成但烟雾验收失败，将其
移动到废纸篓或补丁器备份目录，不删除官方应用或账号状态。完成后清理本任务
临时 ASAR、构建目录和测试进程；保留 workspace 源码、提交和必要诊断记录。
