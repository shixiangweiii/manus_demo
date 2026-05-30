本文介绍通过SDK接入无影AgentBay的流程，涵盖SDK安装、API密钥获取与配置，并提供两个示例以演示无影AgentBay的基本功能。

## 准备工作

-   **账号与权限：**[注册阿里云账号](https://help.aliyun.com/zh/account/ali-cloud-account-registration-process#topic-2149078)，并完成[个人实名认证](https://help.aliyun.com/zh/account/verify-your-identity-individual-account#topic-2149079)或[企业实名认证](https://help.aliyun.com/zh/account/verify-your-identity-enterprise-account/#topic-2149080)。
    
-   **新用户试用：**阿里云账号首次试用无影AgentBay可申请[新用户试用](https://help.aliyun.com/zh/agentbay/product-overview/new-user-trial-instructions)。
    

## **安装SDK并配置环境**

### **安装SDK**

#### **Python**

##### **环境要求**

Python `3.10`及以上版本。

##### **推荐：使用虚拟环境**

```
# Create and activate virtual environment
python3 -m venv agentbay-env
source agentbay-env/bin/activate  # Linux/macOS
# agentbay-env\Scripts\activate   # Windows

# Install the package
pip install wuying-agentbay-sdk

# Verify installation
python -c "import agentbay; print('Installation successful')"
```

##### **替代方案：使用系统 Python（如果允许）**

```
# Install with user flag (if system allows)
pip install --user wuying-agentbay-sdk

# Verify installation 
python -c "import agentbay; print('Installation successful')"
```

#### **TypeScript/JavaScript**

##### **环境要求**

Node.js `14`及以上版本。

```
# Initialize project (if new project)
mkdir my-agentbay-project && cd my-agentbay-project
npm init -y

# Install the package
npm install wuying-agentbay-sdk

# Verify installation
node -e "const {AgentBay} = require('wuying-agentbay-sdk'); console.log('Installation successful')"
```

#### **Golang**

##### **环境要求**

Go `1.24.4`及以上版本。

```
# Initialize module (if new project)
mkdir my-agentbay-project && cd my-agentbay-project  
go mod init my-agentbay-project

# Install the package
GOPROXY=direct go get github.com/aliyun/wuying-agentbay-sdk/golang/pkg/agentbay

# Verify installation
go list -m github.com/aliyun/wuying-agentbay-sdk/golang && echo "Installation successful"
```

### **配置API密钥**

#### **获取API密钥**

1.  前往[无影 AgentBay 控制台](https://agentbay.console.aliyun.com/service-management)。
    
2.  在左侧导航栏选择**服务管理**，单击目标API KEY的复制按钮。
    
    **说明**
    
    若无可用API KEY，单击**创建API KEY**，输入名称并单击**确定**，创建一个API KEY。
    

#### **设置环境变量**

**Linux/macOS：**

```
export AGENTBAY_API_KEY=your_api_key_here
```

**Windows：**

```
setx AGENTBAY_API_KEY your_api_key_here
```

### **安装验证**

SDK安装并完成API配置后，执行如下示例代码，当控制台返回`Test completed successfully`时，说明SDK安装与环境配置成功。

#### **Python**

```
import os
from agentbay import AgentBay

# Get API key from environment
api_key = os.getenv("AGENTBAY_API_KEY")
if not api_key:
    print("Please set AGENTBAY_API_KEY environment variable")
    exit(1)

try:
    # Initialize SDK
    agent_bay = AgentBay(api_key=api_key)
    print("SDK initialized successfully")
    
    # Create a session (requires valid API key and network)
    session_result = agent_bay.create()
    if session_result.success:
        session = session_result.session
        print(f"Session created: {session.session_id}")
        
        # Clean up
        agent_bay.delete(session)
        print("Test completed successfully")
    else:
        print(f"Session creation failed: {session_result.error_message}")
        
except Exception as e:
    print(f"Error: {e}")
```

#### **TypeScript**

```
import { AgentBay } from 'wuying-agentbay-sdk';

const apiKey = process.env.AGENTBAY_API_KEY;
if (!apiKey) {
    console.log("Please set AGENTBAY_API_KEY environment variable");
    process.exit(1);
}

async function test() {
    try {
        // Initialize SDK
        const agentBay = new AgentBay({ apiKey });
        console.log("SDK initialized successfully");
        
        // Create a session (requires valid API key and network)
        const sessionResult = await agentBay.create();
        if (sessionResult.success) {
            const session = sessionResult.session;
            console.log(`Session created: ${session.sessionId}`);
            
            // Clean up
            await agentBay.delete(session);
            console.log("Test completed successfully");
        } else {
            console.log(`Session creation failed: ${sessionResult.errorMessage}`);
        }
    } catch (error) {
        console.log(`Error: ${error}`);
    }
}

test();
```

#### **Golang**

```
package main

import (
    "fmt"
    "os"
    "github.com/aliyun/wuying-agentbay-sdk/golang/pkg/agentbay"
)

func main() {
    // Get API key from environment
    apiKey := os.Getenv("AGENTBAY_API_KEY")
    if apiKey == "" {
        fmt.Println("Please set AGENTBAY_API_KEY environment variable")
        return
    }

    // Initialize SDK
    client, err := agentbay.NewAgentBay(apiKey, nil)
    if err != nil {
        fmt.Printf("Failed to initialize SDK: %v\n", err)
        return
    }
    fmt.Println("Test completed successfully")

    // Create a session (requires valid API key and network)
    sessionResult, err := client.Create(nil)
    if err != nil {
        fmt.Printf("Session creation failed: %v\n", err)
        return
    }
    
    if sessionResult.Session != nil {
        fmt.Printf("Session created: %s\n", sessionResult.Session.SessionID)
        
        // Clean up
        _, err = client.Delete(sessionResult.Session, false)
        if err != nil {
            fmt.Printf("Session cleanup failed: %v\n", err)
        } else {
            fmt.Println("Test completed successfully")
        }
    }
}
```

### **故障排除**

#### **Python问题**

1.  `**externally-managed-environment**`**错误：**
    
    ```
    # 解决方案：使用虚拟环境
    python3 -m venv agentbay-env
    source agentbay-env/bin/activate
    pip install wuying-agentbay-sdk
    ```
    
2.  `**ModuleNotFoundError: No module named 'agentbay'**`**：**
    
    ```
    # 检查虚拟环境是否已激活
    which python  # Should show venv path
    # 如有需要重新安装
    pip install --force-reinstall wuying-agentbay-sdk
    ```
    

#### **TypeScript 问题**

1.  `**Cannot find module 'wuying-agentbay-sdk'**`**：**
    
    ```
    # 确保目前位于包含 package.json 的项目目录中
    pwd
    ls package.json  # 验证package.json文件存在
    # 如有需要重新安装
    npm install wuying-agentbay-sdk
    ```
    
2.  `**require() is not defined**`**：**
    
    ```
    # 验证 Node.js version (需要 14+)
    node --version
    # 确保正在使用 CommonJS（默认）或更新为 ES 模块
    ```
    

#### **Golang 问题**

1.  `**checksum mismatch**`**错误（最常见）：**
    
    ```
    # 始终为此包使用直接代理
    GOPROXY=direct go get github.com/aliyun/wuying-agentbay-sdk/golang/pkg/agentbay
    ```
    
2.  **导入路径错误：**
    
    ```
    # 检查 Go version (需要 1.24.4+)
    go version
    # Ensure module is initialized
    go mod init your-project-name
    ```
    
3.  **构建失败：**
    
    ```
    # 清理模块缓存并重试
    go clean -modcache
    go mod tidy
    go get github.com/aliyun/wuying-agentbay-sdk/golang/pkg/agentbay
    ```
    

#### **网络和 API 问题**

1.  **连接超时：**
    
    -   检查网络连接。
        
    -   验证 API 网关端点是否适合您的位置。
        
    -   如果可以的话，尝试不同的网关端点以获得更好的连接。
        
2.  **API 密钥错误：**
    
    -   验证 API 密钥是否正确且有效。
        
    -   在控制台中检查 API 密钥权限。
        
    -   确保环境变量设置正确。
        
3.  **会话创建失败：**
    
    -   验证账户是否有足够的配额。
        
    -   在控制台检查服务状态。
        
    -   过几分钟后再尝试。
        

## **体验AgentBay工作流**

### **在AgentBay代码沙箱中运行Python代码**

以下代码示例展示了如何通过AgentBay Python SDK创建代码沙箱环境，并运行包含计算的Python代码。

**说明**

将`your-api-key`替换为[获取的API密钥](#e4dcee4d16lg1)。

```
from agentbay import AgentBay
from agentbay import CreateSessionParams

agent_bay = AgentBay(api_key="your-api-key")
session_params = CreateSessionParams(image_id="code_latest")
result = agent_bay.create(session_params)

if result.success:
    session = result.session
    
    code = """
import math

# Calculate factorial
def factorial(n):
    return math.factorial(n)

# Fibonacci sequence
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

print(f"Factorial of 10: {factorial(10)}")
print(f"Fibonacci of 10: {fibonacci(10)}")

# List comprehension
squares = [x**2 for x in range(1, 11)]
print(f"Squares: {squares}")
"""
    
    result = session.code.run_code(code, "python")
    if result.success:
        print("Output:", result.result)
        # Output: Factorial of 10: 3628800
        #         Fibonacci of 10: 55
        #         Squares: [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]
    
    agent_bay.delete(session)
 
```

### **在AgentBay云浏览器沙箱环境中访问阿里云官网**

以下代码示例展示了如何通过AgentBay Python SDK创建云浏览器沙箱环境，并在云浏览器沙箱中访问阿里云。

**重要**

-   该示例代码需要调用`get_endpoint_url`方法获取沙箱环境的可访问链接，该方法不支持Basic权益包订阅使用，需先订阅高级权益后使用，详情请参见[权益包概述](https://help.aliyun.com/zh/agentbay/product-overview/agentbay-billing-instructions#5e33a30f4dx5b)。
    
-   示例代码运行后可打开`resource_url`查看沙箱环境的运行情况。
    

```
import os
import time
from agentbay import AgentBay
from agentbay import CreateSessionParams
from agentbay.browser.browser import BrowserOption
from playwright.sync_api import sync_playwright

def main():
    api_key = os.getenv("AGENTBAY_API_KEY")
    if not api_key:
        raise RuntimeError("AGENTBAY_API_KEY environment variable not set")

    agent_bay = AgentBay(api_key=api_key)

    # Create a session (use an image with browser preinstalled)
    params = CreateSessionParams(image_id="browser_latest")
    session_result = agent_bay.create(params)
    if not session_result.success:
        raise RuntimeError(f"Failed to create session: {session_result.error_message}")

    session = session_result.session

    # Initialize browser (supports stealth, proxy, fingerprint, etc. via BrowserOption)
    ok = session.browser.initialize(BrowserOption())
    if not ok:
        raise RuntimeError("Browser initialization failed")

    endpoint_url = session.browser.get_endpoint_url()

    # Connect Playwright over CDP and automate
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint_url)
        context = browser.contexts[0]
        page = context.new_page()
        page.goto("https://www.aliyun.com")
        print("Title:", page.title())
        time.sleep(30)
        browser.close()
        
    session.delete()

if __name__ == "__main__":
    main()
```

## **后续步骤**

### **了解核心概念**

-   [AgentBay核心概念](https://help.aliyun.com/zh/agentbay/developer-reference/core-concept)：在正式开始编程之前，充分理解AgentBay核心概念可以有效帮助理解不同的方法的设计目的、能力及基本使用方法。
    

### **体验核心能力**

-   [**Command Execution**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/codespace/code-execution.md):运行 Shell 命令和代码。
    
-   [**File Operations**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/common-features/basics/file-operations.md)：体验文件上传、下载和管理。
    
-   [**Session Management**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/common-features/basics/session-management.md)：更多高级的会话参数以及最佳实践。
    

### **探索使用场景**

-   [**Browser Automation**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/computer-use/computer-ui-automation.md)：网页信息抓取与自动化测试。
    
-   [**Mobile Testing**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/mobile-use/mobile-ui-automation.md)：安卓手机应用的自动化验证。
    
-   [**Code Development**](https://github.com/agentbay-ai/wuying-agentbay-sdk/blob/main/docs/guides/codespace/code-execution.md)：探索云端开发环境。
    

### **更多高级配置**

-   [配置Endpoint](https://help.aliyun.com/zh/agentbay/developer-reference/advanced-configuration)：SDK默认使用上海Endpoint，如果需要通过其他区域（例如新加坡）进行连接，需要配置不同的端点以获得更好的网络性能。
    
-   [使用自定义镜像](https://help.aliyun.com/zh/agentbay/manage-images/#615046bf6eve1)：在实际使用与开发过程中，如果遇到网络性能不足，默认镜像缺少所需应用等原因时，可通过使用自定义镜像解决相关问题。