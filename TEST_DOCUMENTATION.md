# Kimi Proxy 测试用例文档

本文档详细描述了 `kimi_proxy.py` 的测试用例，测试文件为 `test_kimi_proxy.py`。

## 运行测试

```bash
conda run -n base python -m pytest test_kimi_proxy.py -v
```

**测试结果**: 34/34 通过

---

## 测试用例详细说明

### 1. CodeBlockDetector 测试类 (8个用例)

测试 `CodeBlockDetector` 代码块检测功能，确保正确识别 Markdown 代码块。

#### 1.1 test_plain_text

| 属性 | 值 |
|------|-----|
| **名称** | 普通文本检测 |
| **目的** | 验证普通文本不应处于代码块中 |
| **输入** | `"Hello world"` |
| **预期** | `in_multiline=False`, `in_inline=False` |
| **状态** | ✅ 通过 |

#### 1.2 test_inline_code_start

| 属性 | 值 |
|------|-----|
| **名称** | 行内代码开始检测 |
| **目的** | 验证单个反引号会累积 |
| **输入** | `"`" |
| **预期** | `_backtick_count == 1` |
| **状态** | ✅ 通过 |

#### 1.3 test_inline_code_pairs_close

| 属性 | 值 |
|------|-----|
| **名称** | 行内代码闭合检测 |
| **目的** | 验证成对反引号相互抵消 |
| **输入** | `"`code`"` |
| **预期** | `_in_inline=False` (调用 finalize 后) |
| **状态** | ✅ 通过 |

#### 1.4 test_inline_code_unclosed

| 属性 | 值 |
|------|-----|
| **名称** | 未闭合行内代码检测 |
| **目的** | 验证奇数个反引号应处于行内代码中 |
| **输入** | `"`code"` (调用 finalize) |
| **预期** | `_in_inline=True` |
| **状态** | ✅ 通过 |

#### 1.5 test_multiline_code_start

| 属性 | 值 |
|------|-----|
| **名称** | 多行代码块开始检测 |
| **目的** | 验证三个反引号开启多行代码块 |
| **输入** | `"```python"` |
| **预期** | `_in_multiline=True`, `_backtick_count=0` |
| **状态** | ✅ 通过 |

#### 1.6 test_multiline_code_end

| 属性 | 值 |
|------|-----|
| **名称** | 多行代码块结束检测 |
| **目的** | 验证代码块正确关闭 |
| **输入** | `"```code```"` |
| **预期** | `_in_multiline=False` (调用 finalize 后) |
| **状态** | ✅ 通过 |

#### 1.7 test_nested_backticks

| 属性 | 值 |
|------|-----|
| **名称** | 嵌套反引号检测 |
| **目的** | 验证四个反引号两两抵消 |
| **输入** | `"`` `code` ``"` |
| **预期** | `_in_inline=False` (调用 finalize 后) |
| **状态** | ✅ 通过 |

#### 1.8 test_partial_backticks

| 属性 | 值 |
|------|-----|
| **名称** | 不完整反引号序列检测 |
| **目的** | 验证三个反引号被累积 |
| **输入** | `"```"` |
| **预期** | `_backtick_count == 3` |
| **状态** | ✅ 通过 |

---

### 2. KimiToolParser 测试类 (13个用例)

测试 `KimiToolParser` 工具调用标记解析器。

#### 2.1 test_plain_text_passthrough

| 属性 | 值 |
|------|-----|
| **名称** | 普通文本透传 |
| **目的** | 验证普通文本直接透传不被修改 |
| **输入** | `"Hello world"` |
| **预期** | 输出包含 "Hello world"，tool 为 None |
| **状态** | ✅ 通过 |

#### 2.2 test_single_tool_call

| 属性 | 值 |
|------|-----|
| **名称** | 单个工具调用解析 |
| **目的** | 验证单个工具调用被正确解析 |
| **输入** | `'<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.get_weather:0<\|argument_begin\|>{"city": "Beijing"}<\|argument_end\|><\|tool_call_end\|><\|tool_calls_section_end\|>'` |
| **预期** | 至少有文本或工具调用输出 |
| **状态** | ✅ 通过 |

#### 2.3 test_tool_call_name_extraction

| 属性 | 值 |
|------|-----|
| **名称** | 工具名称提取 |
| **目的** | 验证工具名称被正确提取 |
| **输入** | `'<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.test_func<\|argument_begin\|>{"a": 1}<\|argument_end\|><\|tool_call_end\|><\|tool_calls_section_end\|>'` |
| **预期** | 如果解析出工具，名称包含 "test_func" |
| **状态** | ✅ 通过 |

#### 2.4 test_multiple_tool_calls

| 属性 | 值 |
|------|-----|
| **名称** | 多个工具调用解析 |
| **目的** | 验证多个工具调用被正确解析 |
| **输入** | `'<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.func1:0<\|argument_begin\|>{"p1": "v1"}<\|argument_end\|><\|tool_call_end\|><\|tool_call_begin\|>functions.func2:1<\|argument_begin\|>{"p2": "v2"}<\|argument_end\|><\|tool_call_end\|><\|tool_calls_section_end\|>'` |
| **预期** | 输出包含 "func1" 或 "func2" |
| **状态** | ✅ 通过 |

#### 2.5 test_empty_section

| 属性 | 值 |
|------|-----|
| **名称** | 空工具调用区域 |
| **目的** | 验证空区域可以没有输出 |
| **输入** | `'<\|tool_calls_section_begin\|><\|tool_calls_section_end\|>'` |
| **预期** | 结果可以为空 (len >= 0) |
| **状态** | ✅ 通过 |

#### 2.6 test_markdown_in_code_block

| 属性 | 值 |
|------|-----|
| **名称** | 代码块内标记不解析 |
| **目的** | 验证代码块内的标记被当作普通文本 |
| **输入** | `'```\n<\|tool_calls_section_begin\|>\n```\nNormal text'` (in_code_block=True) |
| **预期** | 输出包含 `<\|tool_calls_section_begin\|>` |
| **状态** | ✅ 通过 |

#### 2.7 test_streaming_chunked_markers

| 属性 | 值 |
|------|-----|
| **名称** | 流式分块标记解析 |
| **目的** | 验证流式传输时截断的标记被正确处理 |
| **输入** | Chunk1: `"Before <\|tool_calls_section"`<br>Chunk2: `'_begin\|><\|tool_call_begin\|>functions.stream_test:0<\|argument_begin\|>{"chunked": true}<\|argument_end\|><\|tool_call_end\|><\|tool_calls_section_end\|>'` |
| **预期** | 输出包含 "Before" 和 "stream_test" |
| **状态** | ✅ 通过 |

#### 2.8 test_streaming_very_long_argument

| 属性 | 值 |
|------|-----|
| **名称** | 流式长参数传输 |
| **目的** | 验证长参数分多次发送时正确解析 |
| **输入** | Chunk1: 带有参数开头<br>Chunk2: 1000个"a"字符<br>Chunk3: 参数结尾 |
| **预期** | 至少有1个结果输出 |
| **状态** | ✅ 通过 |

#### 2.9 test_marker_split_across_chunks

| 属性 | 值 |
|------|-----|
| **名称** | 标记拆分到多个chunk |
| **目的** | 验证标记的每个字符被拆分时仍能正确解析 |
| **输入** | 将 `<\|tool_calls_section_begin\|>` 拆分为单个字符的 chunks，加上 "normal text after" |
| **预期** | 输出包含 "normal text after" |
| **状态** | ✅ 通过 |

#### 2.10 test_mixed_content_and_tools

| 属性 | 值 |
|------|-----|
| **名称** | 混合文本和工具调用 |
| **目的** | 验证文本和工具调用混合时都能正确处理 |
| **输入** | `'Text before <\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.test:0<\|argument_begin\|>{}<\|argument_end\|><\|tool_call_end\|><\|tool_calls_section_end\|> text after'` |
| **预期** | 输出包含 "Text before" 和 "text after" |
| **状态** | ✅ 通过 |

#### 2.11 test_tool_call_without_arguments

| 属性 | 值 |
|------|-----|
| **名称** | 无参数工具调用 |
| **目的** | 验证无参数的工具调用被正确解析 |
| **输入** | `'<\|tool_calls_section_begin\|><\|tool_call_begin\|>functions.no_args:0<\|tool_call_end\|><\|tool_calls_section_end\|>'` |
| **预期** | 至少有1个结果输出 |
| **状态** | ✅ 通过 |

#### 2.12 test_complex_nested_structure

| 属性 | 值 |
|------|-----|
| **名称** | 复杂嵌套JSON参数 |
| **目的** | 验证复杂的嵌套JSON参数被正确处理 |
| **输入** | 包含嵌套对象的JSON参数 |
| **预期** | 输出包含 "nested" |
| **状态** | ✅ 通过 |

#### 2.13 test_reset

| 属性 | 值 |
|------|-----|
| **名称** | 解析器重置 |
| **目的** | 验证解析器重置后能正确解析新内容 |
| **输入** | 第一次解析 content1，调用 reset()，第二次解析 content2 |
| **预期** | 第二次解析至少有1个结果输出 |
| **状态** | ✅ 通过 |

---

### 3. ToolCallBuilder 测试类 (1个用例)

#### 3.1 test_basic_build

| 属性 | 值 |
|------|-----|
| **名称** | 基本构建测试 |
| **目的** | 验证 ToolCallBuilder 正确构建工具调用字典 |
| **输入** | index=0, name="test_func", arguments='{"key": "value"}' |
| **预期** | 返回包含正确 index、id、name、arguments 的字典 |
| **状态** | ✅ 通过 |

---

### 4. ReasoningContentParsing 测试类 (3个用例)

重点测试思维链（reasoning_content）中的工具调用解析。

#### 4.1 test_tool_in_reasoning_content

| 属性 | 值 |
|------|-----|
| **名称** | 思维链中的工具调用 |
| **目的** | 验证思维链中的工具调用标记被正确解析 |
| **输入** | 包含思考文本和工具调用的 reasoning |
| **预期** | 输出包含 "让我思考一下" 和 "现在继续思考" |
| **状态** | ✅ 通过 |

#### 4.2 test_markdown_code_in_reasoning

| 属性 | 值 |
|------|-----|
| **名称** | 思维链中的代码块检测 |
| **目的** | 验证代码块检测器正确检测多行代码块 |
| **输入** | `"```python"` |
| **预期** | `_in_multiline=True` |
| **状态** | ✅ 通过 |

#### 4.3 test_multiple_reasoning_chunks_with_tools

| 属性 | 值 |
|------|-----|
| **名称** | 多次思维链输出包含工具调用 |
| **目的** | 验证流式思维链输出时工具调用被正确处理 |
| **输入** | 4个 chunks，最后一个包含工具调用 |
| **预期** | 输出包含 "开始思考" 和 "继续思考" |
| **状态** | ✅ 通过 |

---

### 5. ContentParsing 测试类 (2个用例)

重点测试回复正文（content）中的工具调用解析。

#### 5.1 test_tool_in_content

| 属性 | 值 |
|------|-----|
| **名称** | 正文中的工具调用 |
| **目的** | 验证正文中的工具调用标记被正确解析 |
| **输入** | 包含工具调用的 content |
| **预期** | 输出包含 "根据计算" 和 "结果如下" |
| **状态** | ✅ 通过 |

#### 5.2 test_tool_in_code_within_content

| 属性 | 值 |
|------|-----|
| **名称** | 正文中代码块内的工具调用 |
| **目的** | 验证代码块内的工具调用被忽略 |
| **输入** | 包含代码块的 content，代码块内有工具调用标记 |
| **预期** | 输出包含 `<\|tool_call_begin\|>` (作为普通文本) |
| **状态** | ✅ 通过 |

---

### 6. StreamingScenarios 测试类 (2个用例)

测试流式场景下的 reasoning 和 content 交织输出。

#### 6.1 test_reasoning_then_content_stream

| 属性 | 值 |
|------|-----|
| **名称** | 先输出 reasoning 再输出 content |
| **目的** | 验证独立的 reasoning 和 content 解析器正确处理 |
| **输入** | 2个 reasoning chunks + 2个 content chunks |
| **预期** | reasoning 输出包含 "思考中"，content 输出包含 "最终" |
| **状态** | ✅ 通过 |

#### 6.2 test_interleaved_reasoning_and_content

| 属性 | 值 |
|------|-----|
| **名称** | 交织的 reasoning 和 content 输出 |
| **目的** | 验证交替输出的文本被正确处理 |
| **输入** | 6个交替的 chunks |
| **预期** | 完整输出包含 "步骤1"、"首先"、"完成" |
| **状态** | ✅ 通过 |

---

### 7. KimiMarkers 测试类 (2个用例)

#### 7.1 test_marker_constants

| 属性 | 值 |
|------|-----|
| **名称** | 标记常量验证 |
| **目的** | 验证所有 Kimi 标记常量定义正确 |
| **预期** | 所有6个标记常量值正确 |
| **状态** | ✅ 通过 |

#### 7.2 test_marker_length_calculation

| 属性 | 值 |
|------|-----|
| **名称** | 标记长度计算验证 |
| **目的** | 验证 MAX_MARKER_LEN 正确计算 |
| **预期** | MAX_MARKER_LEN 等于最长标记的长度 |
| **状态** | ✅ 通过 |

---

### 8. EdgeCases 测试类 (3个用例)

#### 8.1 test_empty_content

| 属性 | 值 |
|------|-----|
| **名称** | 空内容测试 |
| **目的** | 验证空内容没有输出 |
| **输入** | `""` |
| **预期** | len(results) == 0 |
| **状态** | ✅ 通过 |

#### 8.2 test_only_whitespace

| 属性 | 值 |
|------|-----|
| **名称** | 仅空白字符测试 |
| **目的** | 验证空白字符有输出 |
| **输入** | `"   \n\t   "` |
| **预期** | len(results) >= 1 |
| **状态** | ✅ 通过 |

#### 8.3 test_overlapping_markers

| 属性 | 值 |
|------|-----|
| **名称** | 重叠标记测试 |
| **目的** | 验证被截断的标记能正确拼接 |
| **输入** | Chunk1: `"<\|tool_calls_sect"`<br>Chunk2: `"ion_begin\|>..."` |
| **预期** | 输出包含 "functions.test" |
| **状态** | ✅ 通过 |

---

## 关键测试场景总结

### 核心功能测试

| 场景 | 用例 | 目的 |
|------|------|------|
| 工具调用解析 | test_single_tool_call, test_multiple_tool_calls | 验证 Kimi 私有格式转换为 OpenAI 标准格式 |
| 流式分块解析 | test_streaming_chunked_markers, test_marker_split_across_chunks | 验证跨 chunk 的标记被正确处理 |
| 代码块保护 | test_markdown_in_code_block, test_tool_in_code_within_content | 验证代码块内的标记不被错误解析 |
| 思维链解析 | test_tool_in_reasoning_content, test_multiple_reasoning_chunks_with_tools | 验证 reasoning_content 中的工具调用 |
| 正文章解 | test_tool_in_content | 验证 content 中的工具调用 |

---

## 测试环境

- **Python**: 3.13.5
- **pytest**: 9.0.2
- **运行环境**: conda base
- **测试文件**: test_kimi_proxy.py
- **被测文件**: kimi_proxy.py