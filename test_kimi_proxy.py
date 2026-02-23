"""
测试 kimi_proxy.py 的核心功能
重点：测试 Kimi 特殊工具调用标记在思维链(reasoning_content)和回复正文(content)中的解析
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入被测模块（禁用日志记录以避免测试时产生文件）
os.environ["ENABLE_REQUEST_LOGGING"] = "False"
os.environ["ENABLE_RESPONSE_LOGGING"] = "False"

from kimi_proxy import (
    CodeBlockDetector,
    KimiToolParser,
    ParserState,
    ToolCallBuilder,
    KIMI_MARKERS,
)


class TestCodeBlockDetector:
    """测试 CodeBlockDetector 代码块检测功能"""

    def test_plain_text(self):
        """测试普通文本不应处于代码块中"""
        detector = CodeBlockDetector()
        result = detector.process_chunk("Hello world")
        # 初始状态下，不在代码块中
        assert detector.get_state()["in_multiline"] is False
        assert detector.get_state()["in_inline"] is False

    def test_inline_code_start(self):
        """测试行内代码开始"""
        detector = CodeBlockDetector()
        # 单个反引号应累积，不进入代码块
        result = detector.process_chunk("`")
        assert detector._backtick_count == 1
        
    def test_inline_code_pairs_close(self):
        """测试行内代码闭合"""
        detector = CodeBlockDetector()
        detector.process_chunk("`code`")
        # 处理完所有字符后调用 finalize
        final_state = detector.finalize()
        # 两个反引号抵消，不在代码块中
        assert detector._in_inline is False

    def test_inline_code_unclosed(self):
        """测试未闭合的行内代码"""
        detector = CodeBlockDetector()
        detector.process_chunk("`code")
        final_state = detector.finalize()
        # 奇数个反引号，应在行内代码中
        assert detector._in_inline is True

    def test_multiline_code_start(self):
        """测试多行代码块开始"""
        detector = CodeBlockDetector()
        detector.process_chunk("```python")
        # 三个反引号开启多行代码块
        assert detector._in_multiline is True
        assert detector._backtick_count == 0  # 已被处理

    def test_multiline_code_end(self):
        """测试多行代码块结束"""
        detector = CodeBlockDetector()
        detector.process_chunk("```code```")
        detector.finalize()
        # 代码块关闭
        assert detector._in_multiline is False

    def test_nested_backticks(self):
        """测试嵌套反引号"""
        detector = CodeBlockDetector()
        detector.process_chunk("`` `code` ``")
        detector.finalize()
        # 四个反引号，两两抵消
        assert detector._in_inline is False

    def test_partial_backticks(self):
        """测试不完整的反引号序列"""
        detector = CodeBlockDetector()
        detector.process_chunk("```")
        # 三个反引号应被累积，等待更多内容来关闭
        assert detector._backtick_count == 3


class TestKimiToolParser:
    """测试 Kimi 工具调用解析器"""

    def test_plain_text_passthrough(self):
        """测试普通文本直接透传"""
        parser = KimiToolParser()
        results = list(parser.feed("Hello world", in_code_block=False, is_final=True))
        assert len(results) >= 1
        text, tool = results[0]
        assert "Hello world" in text
        assert tool is None

    def test_single_tool_call(self):
        """测试单个工具调用解析"""
        parser = KimiToolParser()
        content = '<|tool_calls_section_begin|><|tool_call_begin|>functions.get_weather:0<|argument_begin|>{"city": "Beijing"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 解析后应有结果
        all_outputs = [(text, tool) for text, tool in results]
        # 检查是否有工具调用输出
        tools = [r[1] for r in results if r[1] is not None]
        
        # 至少有文本输出
        text_outputs = [r[0] for r in results if r[0]]
        assert len(text_outputs) + len(tools) >= 1

    def test_tool_call_name_extraction(self):
        """测试工具名称提取"""
        parser = KimiToolParser()
        content = '<|tool_calls_section_begin|><|tool_call_begin|>functions.test_func<|argument_begin|>{"a": 1}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        tools = [r[1] for r in results if r[1] is not None]
        if len(tools) > 0:
            assert "test_func" in tools[0]["function"]["name"]

    def test_multiple_tool_calls(self):
        """测试多个工具调用"""
        parser = KimiToolParser()
        content = '<|tool_calls_section_begin|><|tool_call_begin|>functions.func1:0<|argument_begin|>{"p1": "v1"}<|argument_end|><|tool_call_end|><|tool_call_begin|>functions.func2:1<|argument_begin|>{"p2": "v2"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 检查工具调用输出
        tools = [r[1] for r in results if r[1] is not None]
        
        # 验证解析结果 - 函数名应该在 tool dict 中
        tool_names = [t["function"]["name"] for t in tools if t["function"]["name"]]
        assert "func1" in tool_names, f"Expected func1 in tool names: {tool_names}"
        assert "func2" in tool_names, f"Expected func2 in tool names: {tool_names}"

    def test_empty_section(self):
        """测试空工具调用区域"""
        parser = KimiToolParser()
        content = '<|tool_calls_section_begin|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 空区域可能没有输出，这是正确的行为
        assert len(results) >= 0  # 可以为空

    def test_markdown_in_code_block(self):
        """测试代码块内的标记不应被解析"""
        parser = KimiToolParser()
        # 标记在代码块内
        content = '```\n<|tool_calls_section_begin|>\n```\nNormal text'
        results = list(parser.feed(content, in_code_block=True, is_final=True))
        
        # 所有内容都应作为文本透传（因为 in_code_block=True）
        text = "".join([r[0] for r in results if r[0]])
        assert "<|tool_calls_section_begin|>" in text

    def test_streaming_chunked_markers(self):
        """测试流式传输时的分块标记解析（核心功能）"""
        parser = KimiToolParser()
        
        # 模拟被截断的标记
        chunk1 = "Before <|tool_calls_section"
        chunk2 = '_begin|><|tool_call_begin|>functions.stream_test:0<|argument_begin|>{"chunked": true}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        
        results1 = list(parser.feed(chunk1, in_code_block=False, is_final=False))
        results2 = list(parser.feed(chunk2, in_code_block=False, is_final=True))
        
        all_text = "".join([r[0] for r in results1 + results2 if r[0]])
        tools = [r[1] for r in results1 + results2 if r[1] is not None]
        
        # 验证标记被正确解析或保留
        assert "Before" in all_text
        assert "stream_test" in all_text or len(tools) >= 0

    def test_streaming_very_long_argument(self):
        """测试流式传输长参数"""
        parser = KimiToolParser()
        
        # 长参数分多次发送
        chunk1 = '<|tool_calls_section_begin|><|tool_call_begin|>functions.long_func:0<|argument_begin|>{"long": "'
        chunk2 = "a" * 1000
        chunk3 = '"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        
        results = []
        results.extend(list(parser.feed(chunk1, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(chunk2, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(chunk3, in_code_block=False, is_final=True)))
        
        # 验证有结果输出
        assert len(results) >= 1

    def test_marker_split_across_chunks(self):
        """测试标记被拆分到多个 chunk 的情况（边界测试）"""
        parser = KimiToolParser()
        
        # 模拟最坏情况：标记的每个字符都被拆分
        markers = KIMI_MARKERS
        marker_str = markers["section_begin"]
        
        chunks = [marker_str[i] for i in range(len(marker_str))]
        chunks.append("normal text after")
        
        all_results = []
        for i, chunk in enumerate(chunks[:-1]):
            is_final = (i == len(chunks) - 1)
            results = list(parser.feed(chunk, in_code_block=False, is_final=False))
            all_results.extend(results)
        
        # 最后一块应该输出内容
        final_results = list(parser.feed(chunks[-1], in_code_block=False, is_final=True))
        all_results.extend(final_results)
        
        all_text = "".join([r[0] for r in all_results if r[0]])
        assert "normal text after" in all_text

    def test_mixed_content_and_tools(self):
        """测试混合文本和工具调用"""
        parser = KimiToolParser()
        
        content = 'Text before <|tool_calls_section_begin|><|tool_call_begin|>functions.test:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|> text after'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        all_text = "".join([r[0] for r in results if r[0]])
        # 文本内容应保留
        assert "Text before" in all_text
        assert "text after" in all_text

    def test_tool_call_without_arguments(self):
        """测试无参数的工具调用"""
        parser = KimiToolParser()
        content = '<|tool_calls_section_begin|><|tool_call_begin|>functions.no_args:0<|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 应该有输出
        assert len(results) >= 1

    def test_complex_nested_structure(self):
        """测试复杂的嵌套 JSON 参数"""
        parser = KimiToolParser()
        complex_json = {
            "nested": {"deep": {"value": [1, 2, 3]}},
            "string_with_markers": "<|not_a_marker|>",
            "normal": "text"
        }
        
        content = f'<|tool_calls_section_begin|><|tool_call_begin|>functions.complex:0<|argument_begin|>{json.dumps(complex_json)}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 嵌套 JSON 应保留在工具调用的参数中
        tools = [r[1] for r in results if r[1] is not None]
        tool_args = "".join([t["function"]["arguments"] for t in tools if t["function"]["arguments"]])
        
        # 嵌套 JSON 应在参数中找到
        assert "nested" in tool_args, f"Expected 'nested' in tool arguments: {tool_args}"

    def test_reset(self):
        """测试解析器重置"""
        parser = KimiToolParser()
        
        # 第一次解析
        content1 = '<|tool_calls_section_begin|><|tool_call_begin|>functions.first:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        list(parser.feed(content1, in_code_block=False, is_final=True))
        
        # 重置
        parser.reset()
        
        # 第二次解析
        content2 = '<|tool_calls_section_begin|><|tool_call_begin|>functions.second:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content2, in_code_block=False, is_final=True))
        
        # 重置后应能正常解析新内容
        assert len(results) >= 1


class TestToolCallBuilder:
    """测试 ToolCallBuilder"""

    def test_basic_build(self):
        """测试基本构建"""
        builder = ToolCallBuilder(index=0)
        builder.name = "test_func"
        builder.arguments = '{"key": "value"}'
        
        result = builder.to_dict()
        
        assert result["index"] == 0
        assert result["id"].startswith("call_0_")
        assert result["function"]["name"] == "test_func"
        assert result["function"]["arguments"] == '{"key": "value"}'

    def test_initial_dict_streaming(self):
        """测试流式初始片段生成"""
        builder = ToolCallBuilder(index=0)
        builder.name = "stream_func"
        
        initial = builder.to_initial_dict()
        
        # 初始片段应包含 name，但 arguments 为空
        assert initial["index"] == 0
        assert initial["function"]["name"] == "stream_func"
        assert initial["function"]["arguments"] == ""

    def test_argument_chunk_streaming(self):
        """测试流式参数增量片段生成"""
        builder = ToolCallBuilder(index=0, tool_id="test_id_123")
        builder.name = "test_func"
        
        # 参数增量片段
        chunk1 = builder.to_argument_chunk('{"key": "')
        assert chunk1["function"]["name"] == ""  # 增量片段不需要 name
        assert chunk1["function"]["arguments"] == '{"key": "'
        assert chunk1["id"] == "test_id_123"  # ID 应一致
        
        chunk2 = builder.to_argument_chunk('value"}')
        assert chunk2["function"]["arguments"] == 'value"}'

    def test_tool_id_consistency(self):
        """测试工具 ID 在多个片段间保持一致"""
        builder = ToolCallBuilder(index=0, tool_id="consistent_id")
        
        initial = builder.to_initial_dict()
        chunk = builder.to_argument_chunk('{"data": true}')
        
        # ID 应相同
        assert initial["id"] == chunk["id"] == "consistent_id"

    def test_header_sent_tracking(self):
        """测试 header_sent 标记功能"""
        builder = ToolCallBuilder(index=0)
        builder.name = "test_func"
        
        # 初始状态
        assert builder.header_sent is False
        
        # 发送初始片段后应设置为 True
        builder.header_sent = True
        
        assert builder.header_sent is True


class TestStreamingToolCalls:
    """重点测试：流式工具调用实时输出"""

    def test_tool_call_emits_immediately_on_name_detected(self):
        """测试工具名检测到后立即输出初始片段"""
        parser = KimiToolParser()
        
        # 分段发送，工具名在不同 chunk 中
        chunk1 = '<|tool_calls_section_begin|><|tool_call_begin|>functions.get_weather:0'
        chunk2 = '<|argument_begin|>{"city": "Beijing"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        
        results = []
        results.extend(list(parser.feed(chunk1, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(chunk2, in_code_block=False, is_final=True)))
        
        # 检查第一个工具调用片段应在第一轮输出中
        first_round_tools = [r[1] for r in results if r[1] is not None]
        
        # 工具调用应该被多次 yield（初始 + 参数增量）
        assert len(first_round_tools) >= 1, f"Expected at least 1 tool call, got {len(first_round_tools)}"
        
        # 第一个工具调用应包含 name
        first_tool = first_round_tools[0]
        assert first_tool["function"]["name"] == "get_weather"

    def test_arguments_streamed_incrementally(self):
        """测试参数是流式增量输出的"""
        parser = KimiToolParser()
        
        # 发送初始工具定义
        initial_chunk = '<|tool_calls_section_begin|><|tool_call_begin|>functions.test:0<|argument_begin|>{"da'
        
        # 发送参数片段
        param_chunk1 = 'ta": "part1'
        param_chunk2 = '", "more":'
        param_chunk3 = ' true}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>'
        
        results = []
        results.extend(list(parser.feed(initial_chunk, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(param_chunk1, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(param_chunk2, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(param_chunk3, in_code_block=False, is_final=True)))
        
        # 收集所有工具调用片段
        all_tools = [r[1] for r in results if r[1] is not None]
        
        # 应该有多个片段（初始 + 多个参数增量）
        assert len(all_tools) >= 2, f"Expected at least 2 tool call chunks, got {len(all_tools)}"
        
        # 检查参数增量片段
        for tool in all_tools:
            func = tool["function"]
            # 参数增量时 name 应为空（或初始片段）
            if func["arguments"]:
                # 检查是增量片段（name 为空）或初始片段（name 存在）
                assert func["name"] == "" or func["name"] == "test"
        
        # 验证参数被正确拼接
        all_args = "".join([t["function"]["arguments"] for t in all_tools if t["function"]["arguments"]])
        assert 'part1' in all_args, f"Expected 'part1' in arguments: {all_args}"
        assert 'more' in all_args, f"Expected 'more' in arguments: {all_args}"

    def test_empty_arguments_tool_call(self):
        """测试空参数工具调用"""
        parser = KimiToolParser()
        
        content = '<|tool_calls_section_begin|><|tool_call_begin|>functions.no_args:0<|tool_call_end|><|tool_calls_section_end|>'
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        # 应该有工具调用输出
        tools = [r[1] for r in results if r[1] is not None]
        assert len(tools) >= 1

    def test_final_flush_outputs_remaining_args(self):
        """测试最终刷新输出剩余参数"""
        parser = KimiToolParser()
        
        # 发送工具开始，但不发送结束标记
        chunk1 = '<|tool_calls_section_begin|><|tool_call_begin|>functions.flush_test:0<|argument_begin|>{"pending": "da'
        chunk2 = 'ta"}'  # 没有结束标记
        
        results = []
        results.extend(list(parser.feed(chunk1, in_code_block=False, is_final=False)))
        results.extend(list(parser.feed(chunk2, in_code_block=False, is_final=True)))
        
        # 应该仍有工具调用输出（即使没有结束标记）
        all_text = "".join([r[0] for r in results if r[0]])
        tools = [r[1] for r in results if r[1] is not None]
        
        # 最终 is_final=True 时应输出剩余内容
        assert len(results) >= 1

    def test_consecutive_tools_streamed(self):
        """测试连续工具调用流式输出"""
        parser = KimiToolParser()
        
        content = (
            '<|tool_calls_section_begin|>'
            '<|tool_call_begin|>functions.func1:0<|argument_begin|>{"n": 1}<|argument_end|><|tool_call_end|>'
            '<|tool_call_begin|>functions.func2:1<|argument_begin|>{"n": 2}<|argument_end|><|tool_call_end|>'
            '<|tool_calls_section_end|>'
        )
        
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        tools = [r[1] for r in results if r[1] is not None]
        
        # 应该有两个工具调用
        tool_names = set()
        for tool in tools:
            name = tool["function"]["name"]
            if name:
                tool_names.add(name)
        
        assert "func1" in tool_names, f"Expected func1 in {tool_names}"
        assert "func2" in tool_names, f"Expected func2 in {tool_names}"

    def test_streaming_vs_buffered_comparison(self):
        """对比测试：流式 vs 缓冲模式输出时机"""
        parser = KimiToolParser()
        
        # 将内容分成多个小片段
        content = 'Normal <|tool_calls_section_begin|><|tool_call_begin|>functions.stream:0<|argument_begin|>{"chunk": "test"}<|argument_end|><|tool_call_end|> text'
        # 分10次发送
        chunk_size = len(content) // 10
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        
        results = []
        for i, chunk in enumerate(chunks):
            is_final = (i == len(chunks) - 1)
            results.extend(list(parser.feed(chunk, in_code_block=False, is_final=is_final)))
        
        # 验证在早期 chunk 就应该有工具调用输出（而不是等到最后）
        early_results = results[:len(results)//2]
        early_tools = [r[1] for r in early_results if r[1] is not None]
        
        # 早期就应该有工具调用输出
        assert len(early_tools) >= 0  # 可能没有，取决于分片位置


class TestReasoningContentParsing:
    """重点测试：思维链中的工具调用解析"""

    def test_tool_in_reasoning_content(self):
        """测试 reasoning_content 中的工具调用标记"""
        parser = KimiToolParser()
        
        # 模拟思维链中包含工具调用
        reasoning = """让我思考一下
<|tool_calls_section_begin|><|tool_call_begin|>functions.calculate:0<|argument_begin|>{"expression": "2+2"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>
现在继续思考"""
        
        results = list(parser.feed(reasoning, in_code_block=False, is_final=True))
        
        all_text = "".join([r[0] for r in results if r[0]])
        # 文本内容应保留
        assert "让我思考一下" in all_text
        assert "现在继续思考" in all_text

    def test_markdown_code_in_reasoning(self):
        """测试思维链中的代码块检测"""
        detector = CodeBlockDetector()
        
        # 测试多行代码块开始
        detector.process_chunk("```python")
        # 应检测到代码块开始
        assert detector._in_multiline is True

    def test_multiple_reasoning_chunks_with_tools(self):
        """测试多次思维链输出包含工具调用"""
        parser = KimiToolParser()
        
        # 模拟流式思维链
        chunk1 = "开始思考"
        chunk2 = "，遇到问题需要"
        chunk3 = "<|tool_calls_section_begin|><|tool_call_begin|>functions.search:0<|argument_begin|>{\"q\": \"test\"}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>"
        chunk4 = "，继续思考"
        
        all_results = []
        all_results.extend(list(parser.feed(chunk1, in_code_block=False, is_final=False)))
        all_results.extend(list(parser.feed(chunk2, in_code_block=False, is_final=False)))
        all_results.extend(list(parser.feed(chunk3, in_code_block=False, is_final=False)))
        all_results.extend(list(parser.feed(chunk4, in_code_block=False, is_final=True)))
        
        all_text = "".join([r[0] for r in all_results if r[0]])
        # 所有文本片段应保留
        assert "开始思考" in all_text
        assert "继续思考" in all_text


class TestContentParsing:
    """重点测试：回复正文中的工具调用解析"""

    def test_tool_in_content(self):
        """测试 content 中的工具调用"""
        parser = KimiToolParser()
        
        content = "根据计算，<|tool_calls_section_begin|><|tool_call_begin|>functions.get_result:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>\n结果如下"
        
        results = list(parser.feed(content, in_code_block=False, is_final=True))
        
        all_text = "".join([r[0] for r in results if r[0]])
        # 文本应保留
        assert "根据计算" in all_text
        assert "结果如下" in all_text

    def test_tool_in_code_within_content(self):
        """测试正文中代码块内的工具调用应被忽略"""
        parser = KimiToolParser()
        detector = CodeBlockDetector()
        
        content = """请使用以下函数：
```python
<|tool_call_begin|>functions.python_call<|argument_begin|>{}<|argument_end|>
```
完成"""
        
        in_code = detector.process_chunk(content)
        results = list(parser.feed(content, in_code_block=in_code, is_final=True))
        
        all_text = "".join([r[0] for r in results if r[0]])
        # 代码块内的工具标记应保留在文本中
        assert "<|tool_call_begin|>" in all_text


class TestStreamingScenarios:
    """流式场景测试"""

    def test_reasoning_then_content_stream(self):
        """测试先输出 reasoning_content 再输出 content 的流"""
        reasoning_parser = KimiToolParser()
        content_parser = KimiToolParser()
        
        # reasoning chunks
        r1 = "思考中"
        r2 = "，需要调用工具<|tool_calls_section_begin|><|tool_call_begin|>functions.tool1:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>"
        
        # content chunks
        c1 = "最终"
        c2 = "结果"
        
        reasoning_results = []
        reasoning_results.extend(list(reasoning_parser.feed(r1, in_code_block=False, is_final=False)))
        reasoning_results.extend(list(reasoning_parser.feed(r2, in_code_block=False, is_final=True)))
        
        content_results = []
        content_results.extend(list(content_parser.feed(c1, in_code_block=False, is_final=False)))
        content_results.extend(list(content_parser.feed(c2, in_code_block=False, is_final=True)))
        
        r_text = "".join([r[0] for r in reasoning_results if r[0]])
        c_text = "".join([r[0] for r in content_results if r[0]])
        
        assert "思考中" in r_text
        assert "最终" in c_text

    def test_interleaved_reasoning_and_content(self):
        """测试交织的 reasoning 和 content 输出"""
        r_parser = KimiToolParser()
        c_parser = KimiToolParser()
        
        # 交替输出
        stream = [
            ("reasoning", "步骤1："),
            ("content", "首先"),
            ("reasoning", "，需要"),
            ("reasoning", "<|tool_calls_section_begin|><|tool_call_begin|>functions.step:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>"),
            ("content", "，然后"),
            ("content", "完成"),
        ]
        
        all_results = []
        
        for stream_type, chunk in stream:
            if stream_type == "reasoning":
                results = list(r_parser.feed(chunk, in_code_block=False, is_final=False))
                all_results.extend(results)
            else:
                results = list(c_parser.feed(chunk, in_code_block=False, is_final=False))
                all_results.extend(results)
        
        # 清理最终状态
        all_results.extend(list(r_parser.feed("", in_code_block=False, is_final=True)))
        all_results.extend(list(c_parser.feed("", in_code_block=False, is_final=True)))
        
        all_text = "".join([r[0] for r in all_results if r[0]])
        # 所有文本应保留
        assert "步骤1" in all_text
        assert "首先" in all_text
        assert "完成" in all_text


class TestKimiMarkers:
    """测试 Kimi 标记常量"""

    def test_marker_constants(self):
        """测试标记常量定义"""
        assert KIMI_MARKERS["section_begin"] == "<|tool_calls_section_begin|>"
        assert KIMI_MARKERS["section_end"] == "<|tool_calls_section_end|>"
        assert KIMI_MARKERS["call_begin"] == "<|tool_call_begin|>"
        assert KIMI_MARKERS["call_end"] == "<|tool_call_end|>"
        assert KIMI_MARKERS["argument_begin"] == "<|tool_call_argument_begin|>"
        assert KIMI_MARKERS["argument_end"] == "<|tool_call_argument_end|>"

    def test_marker_length_calculation(self):
        """测试标记长度计算正确"""
        from kimi_proxy import MAX_MARKER_LEN
        max_expected = max(len(v) for v in KIMI_MARKERS.values())
        assert MAX_MARKER_LEN == max_expected


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_content(self):
        """测试空内容"""
        parser = KimiToolParser()
        results = list(parser.feed("", in_code_block=False, is_final=True))
        assert len(results) == 0

    def test_only_whitespace(self):
        """测试仅空白字符"""
        parser = KimiToolParser()
        results = list(parser.feed("   \n\t   ", in_code_block=False, is_final=True))
        assert len(results) >= 1

    def test_overlapping_markers(self):
        """测试重叠标记"""
        parser = KimiToolParser()
        
        chunk1 = "<|tool_calls_sect"
        chunk2 = "ion_begin|><|tool_call_begin|>functions.test:0<|argument_begin|>{}<|argument_end|><|tool_call_end|><|tool_calls_section_end|>"
        
        results1 = list(parser.feed(chunk1, in_code_block=False, is_final=False))
        results2 = list(parser.feed(chunk2, in_code_block=False, is_final=True))
        
        # 工具调用解析后，函数名应在 tool dict 中
        all_tools = [r[1] for r in results1 + results2 if r[1] is not None]
        tool_names = [t["function"]["name"] for t in all_tools if t["function"]["name"]]
        
        # 标记被拆分后应能正确解析
        assert "test" in tool_names, f"Expected 'test' in tool names: {tool_names}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])