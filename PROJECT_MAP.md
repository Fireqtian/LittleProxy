# LittleProxy 项目文件说明

## 核心服务文件

### `kimi_proxy.py`
主代理服务，运行在端口 **8112**。负责将特定厂商的私有工具调用格式转换为 OpenAI 标准格式。

**主要功能：**
- 接收 OpenAI 格式的 API 请求
- 自动添加思维链启用参数
- 解析响应中的特殊标记区域（以特定中文字符开始和结束的区域）
- 将私有格式的函数调用转换为标准 `tool_calls` 格式
- 支持流式响应实时转换
- 代码块保护：避免误解析 Markdown 代码块内的标记

**关键组件：**
- `CodeBlockDetector`: 检测 Markdown 代码块状态
- `KimiToolParser`: 状态机驱动的标记解析器
- `ToolCallBuilder`: 构建 OpenAI 格式工具调用对象

---

### `little_proxy.py`
辅助代理服务，运行在端口 **8111**。提供透明的 API 转发和拦截功能。

**主要功能：**
- 模型映射和请求转发
- 非法工具调用格式拦截（检测特定的起始标记）
- 强制原生工具调用检查
- 代码块过滤（排除代码块内容后再检测）

**关键组件：**
- `StreamBuffer`: 跨 chunk 缓冲区用于检测截断的标记
- `CodeBlockFilter`: 过滤代码块内容的检测器

---

## 配置文件

### `model_mapping.example.json`
配置文件模板，用户需要复制为 `model_mapping.json` 后使用。

**配置项：**
- `default_target_host`: 默认目标 API 主机
- `model_mapping`: 模型名称映射表（别名 → 实际模型信息）
- `logging`: 请求/响应日志配置

---

## 启动脚本

### `start_kimi_proxy.bat`
Windows 批处理脚本，用于启动主代理服务。
- 设置 UTF-8 编码
- 激活 conda base 环境
- 运行 `kimi_proxy.py`

### `start_little_proxy.bat`
Windows 批处理脚本，用于启动辅助代理服务。
- 设置 UTF-8 编码
- 激活 conda base 环境
- 运行 `little_proxy.py`

---

## 测试文件

### `test_kimi_proxy.py`
单元测试套件，覆盖核心解析功能。

**测试类别：**
- `CodeBlockDetector` (8 个用例): 代码块检测
- `KimiToolParser` (13 个用例): 标记解析
- `ToolCallBuilder` (5 个用例): 工具调用构建
- `StreamingToolCalls` (6 个用例): 流式场景
- `ReasoningContentParsing` (3 个用例): 思维链解析
- `ContentParsing` (2 个用例): 正文解析
- `StreamingScenarios` (2 个用例): 交织输出
- `KimiMarkers` (2 个用例): 标记常量
- `EdgeCases` (3 个用例): 边界情况

**运行方式：**
```bash
conda run -n base python -m pytest test_kimi_proxy.py -v
```

---

### `TEST_DOCUMENTATION.md`
详细的测试文档，包含每个测试用例的说明、输入、预期结果和状态。

---

### `test_response.py`
响应测试工具，用于手动测试和调试 API 响应。

---

## 日志目录

### `request_logs/`
请求日志保存目录，按时间戳命名存储接收到的请求内容。

### `response_logs/`
响应日志保存目录，按时间戳命名存储返回的响应内容。

---

## 其他文件

### `README.md`
项目主文档，包含功能特性、快速开始指南、API 使用示例和技术细节说明。

### `.gitignore`
Git 忽略规则，排除日志目录和本地配置文件。

---

## 架构概览

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   客户端应用     │────▶│   kimi_proxy     │────▶│   Kimi API      │
│  (OpenAI SDK)   │◄────│   (Port 8112)    │◄────│                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ 格式转换引擎  │
                        │ - 标记解析   │
                        │ - 代码块保护 │
                        │ - 流式处理   │
                        └──────────────┘
```

## 数据流说明

1. **请求阶段**：客户端发送 OpenAI 格式请求 → 代理添加必要参数 → 转发到目标 API
2. **响应阶段**：
   - 非流式：完整解析响应内容中的特殊区域 → 转换格式 → 返回客户端
   - 流式：实时解析每个 chunk → 即时转换并输出 → 保持 SSE 格式

## 特殊标记说明

项目中处理的私有格式使用以下标记（用文字描述）：
- **区域开始标记**：两个特定中文字符，表示工具调用区域开始
- **区域结束标记**：两个特定中文字符，表示工具调用区域结束
- **调用开始标记**：两个特定中文字符，后跟函数名
- **调用结束标记**：两个特定中文字符加空格，表示单个调用结束
- **参数开始标记**：空格加特定中文字符，表示参数 JSON 开始
- **参数结束标记**：尖括号包裹的特殊 token，表示参数结束
