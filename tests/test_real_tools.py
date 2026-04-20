#!/usr/bin/env python3
"""真实工具调用测试 - 不依赖 LLM"""
import asyncio
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.code_executor import CodeExecutorTool
from tools.file_ops import FileOpsTool

async def test_code_executor():
    """测试代码执行工具"""
    print("Testing CodeExecutorTool...")
    tool = CodeExecutorTool()
    
    # 测试简单计算
    result = await tool.execute(code="print(2 + 2)")
    assert "4" in result or "Output" in result, f"代码执行失败：{result}"
    print(f"✓ 简单计算测试通过：{result[:100]}")
    
    # 测试复杂代码
    code = """
import sys
print(f"Python version: {sys.version}")
result = sum(range(101))
print(f"Sum of 1 to 100: {result}")
"""
    result = await tool.execute(code=code)
    assert "5050" in result, f"复杂代码执行失败：{result}"
    print(f"✓ 复杂代码测试通过")
    
    # 测试错误处理
    result = await tool.execute(code="print(1/0)")
    assert "Error" in result or "error" in result.lower() or "ZeroDivisionError" in result, \
        f"错误处理失败：{result}"
    print(f"✓ 错误处理测试通过")
    
    print("✓ CodeExecutorTool 所有测试通过\n")

async def test_file_ops():
    """测试文件操作工具"""
    print("Testing FileOpsTool...")
    tool = FileOpsTool()
    
    test_filename = "test_ops.txt"
    test_content = "Hello Test! 文件操作测试"
    
    # 测试写入
    write_result = await tool.execute(action="write", filename=test_filename, content=test_content)
    assert "Successfully wrote" in write_result, f"文件写入失败：{write_result}"
    print(f"✓ 文件写入测试通过：{write_result}")
    
    # 测试读取
    read_result = await tool.execute(action="read", filename=test_filename)
    assert test_content in read_result, f"文件读取失败：{read_result}"
    print(f"✓ 文件读取测试通过：{read_result[:100]}")
    
    # 测试列出文件
    list_result = await tool.execute(action="list")
    assert "Files in sandbox" in list_result or "test_ops.txt" in list_result, \
        f"文件列表失败：{list_result}"
    print(f"✓ 文件列表测试通过")
    
    # 测试错误处理 - 读取不存在的文件
    error_result = await tool.execute(action="read", filename="nonexistent_file.txt")
    assert "Error" in error_result, f"错误处理失败：{error_result}"
    print(f"✓ 错误处理测试通过")
    
    # 测试路径穿越保护
    path_traversal_result = await tool.execute(action="read", filename="../../../etc/passwd")
    assert "Error" in path_traversal_result or "Access denied" in path_traversal_result, \
        f"路径穿越保护失败：{path_traversal_result}"
    print(f"✓ 路径穿越保护测试通过")
    
    # 清理测试文件
    try:
        os.remove(os.path.join(tool._sandbox, test_filename))
        print(f"✓ 清理测试文件完成\n")
    except:
        pass
    
    print("✓ FileOpsTool 所有测试通过\n")

async def main():
    """运行所有测试"""
    print("=" * 60)
    print("真实工具调用测试")
    print("=" * 60 + "\n")
    
    try:
        await test_code_executor()
        await test_file_ops()
        
        print("=" * 60)
        print("✓ 所有真实工具测试通过!")
        print("=" * 60)
        return 0
        
    except Exception as e:
        print(f"\n✗ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
