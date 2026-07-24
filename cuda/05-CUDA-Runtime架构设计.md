# CUDA Runtime 架构设计

> 前四章建立了 GPU 硬件基础、编程模型、内存管理和执行控制。一个自然的问题是：这些 API 是如何实现的？Runtime 内部如何管理资源、调度任务、与 Driver 交互？本章深入 CUDA Runtime 的架构设计，揭示其内部工作机制。
>
> **工程师视角**：理解 Runtime 的架构设计，才能理解为什么某些 API 有特定的行为约束（如上下文切换、默认流同步），才能在系统软件开发中做出正确的设计决策。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| Runtime API | — | CUDA 运行时 API，提供简化的接口语义 |
| Driver API | — | CUDA 驱动 API，提供底层控制 |
| Context | — | 上下文，GPU 资源的容器 |
| Module | — | 模块，编译后的代码对象（cubin/fatbin） |
| Function | — | 函数，模块中的可执行代码（Kernel） |
| Primary Context | — | 主上下文，每个设备默认创建的上下文 |
| Per-thread Default Stream | — | 每线程默认流，CUDA 7.0 引入 |
| Legacy Default Stream | — | 遗留默认流，所有线程共享 |
| Lazy Initialization | — | 延迟初始化，首次使用时才创建资源 |
| Reference Counting | — | 引用计数，管理资源生命周期 |

### 5.1 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| CUDA 编程模型（Kernel、Stream、Event） | [02-CUDA编程模型](./02-CUDA编程模型.md)、[04-执行模型与同步机制](./04-执行模型与同步机制.md) |
| 内存管理（cudaMalloc、cudaMemcpy） | [03-内存管理与地址空间](./03-内存管理与地址空间.md) |
| C/C++ 动态链接库基础 | — |

***

## 1. Runtime 在软件栈中的位置

> 在深入 Runtime 内部之前，先理解它在整个 CUDA 软件栈中的位置——它是用户程序与 Driver 之间的桥梁。

### 1.1 软件栈层级

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart TD
    subgraph "应用层"
        App["用户程序<br/>C/C++/Python"]
        Lib["高层库<br/>cuBLAS/cuDNN/NCCL"]
    end
    
    subgraph "Runtime 层"
        Runtime["CUDA Runtime API<br/>libcudart.so"]
    end
    
    subgraph "Driver 层"
        Driver["CUDA Driver API<br/>libcuda.so"]
    end
    
    subgraph "内核模块"
        KMD["NVIDIA 内核驱动<br/>nvidia.ko"]
    end
    
    subgraph "硬件"
        GPU["GPU 硬件"]
    end
    
    App -->|"cudaMalloc/cudaMemcpy"| Runtime
    Lib -->|"cuBLAS/cuDNN"| Runtime
    Runtime -->|"cuMemAlloc/cuMemcpy"| Driver
    Driver -->|"ioctl"| KMD
    KMD -->|"寄存器读写"| GPU
    
    style Runtime fill:#dbeafe,stroke:#2563eb,stroke-width:3px
```

**如何读这张图**：
- **应用层**：用户程序和高层库（如 cuBLAS）
- **Runtime 层**：CUDA Runtime（`libcudart.so`），提供简化的 API
- **Driver 层**：CUDA Driver（`libcuda.so`），提供底层控制
- **内核模块**：NVIDIA 内核驱动（`nvidia.ko`），管理硬件资源
- **硬件**：GPU 硬件

**Runtime 的角色**：Runtime 是 Driver 的封装层，它：
1. 简化了接口语义（如 `cudaMalloc` vs `cuMemAlloc`）
2. 自动管理上下文（延迟初始化）
3. 提供默认行为（如默认流、主上下文）
4. 隐藏了部分底层细节（如模块加载、函数解析）

### 1.2 Runtime vs Driver 的设计权衡

| 对比维度 | Runtime API | Driver API |
|----------|-------------|------------|
| **设计目标** | 易用性 | 灵活性 |
| **上下文管理** | 自动（延迟初始化） | 手动（显式创建/销毁） |
| **模块加载** | 隐式（nvcc 自动处理） | 显式（`cuModuleLoad`） |
| **错误处理** | 简化（部分错误自动恢复） | 完整（所有错误码暴露） |
| **线程安全性** | 部分线程安全 | 需要手动管理 |
| **适用场景** | 应用开发 | 系统软件开发、驱动开发 |

**为什么需要两层 API？**

**历史原因**：CUDA 早期只有 Driver API，但接口语义复杂，应用开发者抱怨难用。NVIDIA 在 CUDA 2.0 引入了 Runtime API，作为 Driver 的封装层。

**设计动机**：
- **Runtime**：面向应用开发者，隐藏底层细节，提供"开箱即用"的体验
- **Driver**：面向系统软件工程师，提供完整的控制能力，支持高级场景（如多上下文、动态模块加载）

**具体例子**：

```c
// Runtime API：简单，自动管理上下文
cudaMalloc((void **)&d_data, size);
cudaMemcpy(d_data, h_data, size, cudaMemcpyHostToDevice);
myKernel<<<blocks, threads>>>(d_data);

// Driver API：复杂，需要手动管理
CUctxCreateParams params = {};  // CUDA 12.0+ 引入
CUcontext context;
cuCtxCreate(&context, &params, 0, device);
CUdeviceptr d_data;
cuMemAlloc(&d_data, size);
cuMemcpyHtoD(d_data, h_data, size);
// cuLaunchKernel 接受 6 个独立的 grid/block 维度分量,不接受 dim3
cuLaunchKernel(myKernel,
               blocks.x, blocks.y, blocks.z,    // gridDimX/Y/Z
               threads.x, threads.y, threads.z, // blockDimX/Y/Z
               0, NULL,                          // sharedMem, stream
               args, NULL);                      // kernelParams, extra
```

> **核心要点**：Runtime 是 Driver 的封装层，它简化了接口语义，但牺牲了部分控制能力。系统软件工程师通常需要直接操作 Driver API，而应用开发者使用 Runtime API 即可。

***

## 2. 上下文管理

> 上下文（Context）是 CUDA 中最重要的概念之一——它是所有 GPU 资源的容器。本节深入上下文的创建、切换、销毁，以及 Runtime 的自动管理机制。

### 2.1 上下文的本质

**定义**：上下文是 GPU 资源的容器，包含：
- 设备内存分配
- 模块（编译后的代码）
- Stream 和 Event
- 纹理和表面引用
- 配置参数（如共享内存大小）

**类比**：上下文类似于操作系统的进程——每个进程有独立的地址空间、文件描述符、线程等。类似地，每个上下文有独立的设备内存、模块、Stream 等。

### 2.2 Driver API 的上下文管理

**创建上下文**（CUDA 12.0+ 引入了 `CUctxCreateParams` 参数,旧签名被软弃用）:

```c
CUdevice device;
cuDeviceGet(&device, 0);

CUctxCreateParams params = {};  // CUDA 12.0+,可为空配置
CUcontext context;
cuCtxCreate(&context, &params, 0, device);
```

> 旧版签名 `cuCtxCreate(&ctx, flags, device)` 在 CUDA 12.0 之前使用,CUDA 12.0+ 仍向后兼容(通过 `cuCtxCreate_v2`),但推荐使用 `CUctxCreateParams` 版本以便启用向量上下文等新特性。

**切换上下文**：

```c
// 创建多个上下文
CUctxCreateParams params = {};
CUcontext ctx0, ctx1;
cuCtxCreate(&ctx0, &params, 0, device0);
cuCtxCreate(&ctx1, &params, 0, device1);

// 切换上下文
cuCtxSetCurrent(ctx0);  // 切换到 ctx0
// 使用 ctx0 的资源...

cuCtxSetCurrent(ctx1);  // 切换到 ctx1
// 使用 ctx1 的资源...
```

**销毁上下文**：

```c
cuCtxDestroy(context);
```

**引用计数**：每个上下文有引用计数，`cuCtxCreate` 增加计数，`cuCtxDestroy` 减少计数。计数为 0 时，上下文被销毁。

### 2.3 Runtime API 的自动管理

**延迟初始化（Lazy Initialization）**：

Runtime 采用延迟初始化策略——首次调用 CUDA API 时才创建上下文。

```c
// 首次调用 cudaMalloc 时，Runtime 自动：
// 1. 初始化 Driver
// 2. 选择设备（默认为 device 0）
// 3. 创建主上下文（Primary Context）
// 4. 将主上下文设置为当前上下文

cudaMalloc((void **)&d_data, size);  // 触发延迟初始化
```

**主上下文（Primary Context）**：

每个设备有一个主上下文，Runtime 自动管理其生命周期。

```c
// 获取主上下文
CUcontext primary;
cuDevicePrimaryCtxRetain(&primary, device);

// 释放主上下文（引用计数减 1）
cuDevicePrimaryCtxRelease(device);

// 重置主上下文（销毁并重新创建）
cuDevicePrimaryCtxReset(device);
```

**主上下文的特性**：
- 每个设备只有一个主上下文
- Runtime 自动创建和销毁
- 引用计数管理（`cuDevicePrimaryCtxRetain` 增加计数）
- 进程退出时自动释放

### 2.4 上下文切换的开销

**问题**：上下文切换需要保存/恢复 GPU 状态，开销较大。

**具体例子**：

```c
// 差：频繁切换上下文
for (int i = 0; i < 100; i++) {
    cuCtxSetCurrent(ctx0);
    // 使用 ctx0...
    
    cuCtxSetCurrent(ctx1);
    // 使用 ctx1...
}

// 好：批量使用，减少切换
cuCtxSetCurrent(ctx0);
for (int i = 0; i < 100; i++) {
    // 使用 ctx0...
}

cuCtxSetCurrent(ctx1);
for (int i = 0; i < 100; i++) {
    // 使用 ctx1...
}
```

**性能影响**：
- 上下文切换开销约 10-100 微秒
- 频繁切换会导致性能下降
- 建议：尽量使用单个上下文，必要时批量切换

### 2.5 多上下文 vs 多进程

**多上下文**：
- 优点：共享进程地址空间，数据传递方便
- 缺点：上下文切换开销，线程安全性需要手动管理

**多进程**：
- 优点：进程隔离，无切换开销
- 缺点：数据传递需要 IPC（进程间通信）

**选择建议**：
- **单设备**：使用单上下文（Runtime 自动管理）
- **多设备**：每个设备一个线程，每个线程一个上下文
- **高性能场景**：多进程，避免上下文切换

> **核心要点**：上下文是 GPU 资源的容器。Runtime 自动管理主上下文，简化了编程；Driver API 提供手动管理，支持高级场景。上下文切换开销大，应尽量避免频繁切换。

***

## 3. 模块与函数管理

> 上下文管理 GPU 资源，模块（Module）管理编译后的代码。本节深入模块的加载、函数的解析，以及 Runtime 的自动管理机制。

### 3.1 模块的本质

**定义**：模块是编译后的代码对象，包含：
- Kernel 函数（`__global__` 函数）
- 设备函数（`__device__` 函数）
- 常量数据（`__constant__` 变量）
- 纹理引用

**编译产物**：
- **PTX**：虚拟指令集，文本格式，可移植
- **cubin**：二进制格式，特定架构
- **fatbin**：包含多个 cubin/PTX，支持多架构

### 3.2 Driver API 的模块管理

**加载模块**：

```c
// 从文件加载
CUmodule module;
cuModuleLoad(&module, "kernel.cubin");

// 从内存加载
cuModuleLoadData(&module, kernel_data);

// 从 fatbin 加载
cuModuleLoadFatBinary(&module, fatbin_data);
```

**获取函数**：

```c
CUfunction kernel;
cuModuleGetFunction(&kernel, module, "myKernel");
```

**卸载模块**：

```c
cuModuleUnload(module);
```

### 3.3 Runtime API 的自动管理

**隐式加载**：

Runtime 在程序启动时自动加载模块（由 nvcc 嵌入的 fatbin）。

```c
// nvcc 编译时，将 fatbin 嵌入可执行文件
// 程序启动时，Runtime 自动加载 fatbin
// 用户无需手动调用 cuModuleLoad

myKernel<<<blocks, threads>>>(args);  // Runtime 自动解析 myKernel
```

**具体流程**：

1. **编译时**：nvcc 将 Kernel 编译为 fatbin，嵌入可执行文件的 `.nvFatBinSegment` 段
2. **启动时**：Runtime 的初始化代码扫描 `.nvFatBinSegment`，注册所有模块
3. **首次调用**：Runtime 加载模块到当前上下文，解析函数符号

**源码示例**（简化的 Runtime 初始化流程）：

```c
// 摘自 CUDA Runtime 的初始化代码（伪代码）
__attribute__((constructor))
void __cuda_runtime_init(void) {
    // 1. 初始化 Driver
    cuInit(0);
    
    // 2. 扫描 .nvFatBinSegment 段
    for (each fatbin in .nvFatBinSegment) {
        // 3. 注册模块
        __cudaRegisterFatBinary(fatbin);
    }
}

// 首次调用 Kernel 时
void __cudaRegisterFunction(void *fatbin, const char *name, ...) {
    // 1. 加载模块到当前上下文
    CUmodule module = cuModuleLoadFatBinary(fatbin);
    
    // 2. 获取函数
    CUfunction func;
    cuModuleGetFunction(&func, module, name);
    
    // 3. 缓存函数指针
    function_cache[name] = func;
}
```

### 3.4 函数调用的内部流程

**Kernel 启动的完整流程**：

```c
myKernel<<<blocks, threads>>>(args);
```

**内部实现**（伪代码）：

```c
// 1. 解析函数
CUfunction func = function_cache["myKernel"];
if (!func) {
    // 首次调用，加载模块并解析
    func = __cudaRegisterFunction(fatbin, "myKernel");
}

// 2. 获取当前上下文
CUcontext context = get_current_context();

// 3. 配置 Kernel 参数
CUlaunchConfig config = {
    .gridDim = blocks,
    .blockDim = threads,
    .sharedMem = 0,
    .stream = 0
};

// 4. 启动 Kernel
cuLaunchKernel(func, config.gridDim, config.blockDim, 
               config.sharedMem, config.stream, args, NULL);
```

**关键步骤**：
1. **函数解析**：从缓存中查找函数，首次调用时加载模块
2. **上下文检查**：确保当前上下文有效
3. **参数配置**：设置 Grid/Block 维度、共享内存大小、Stream
4. **Driver 调用**：调用 `cuLaunchKernel` 启动 Kernel

### 3.5 模块缓存

**问题**：每次调用 Kernel 都要加载模块吗？

**答案**：Runtime 会缓存已加载的模块，避免重复加载。

**缓存策略**：
- **进程级缓存**：每个进程一个缓存，存储已加载的模块
- **上下文级缓存**：每个上下文一个缓存，存储该上下文加载的模块

**具体例子**：

```c
// 第一次调用
myKernel<<<blocks, threads>>>(args);
// Runtime 加载模块，缓存到 function_cache

// 第二次调用
myKernel<<<blocks, threads>>>(args);
// Runtime 从 function_cache 查找，直接使用

// 切换上下文后调用
cuCtxSetCurrent(ctx1);
myKernel<<<blocks, threads>>>(args);
// Runtime 检查 ctx1 的缓存，如果没有则加载模块
```

> **核心要点**：Runtime 自动管理模块和函数的加载、解析、缓存。用户无需手动调用 `cuModuleLoad`，但理解内部流程有助于调试性能问题（如首次调用延迟）。

***

## 4. 默认流的行为

> 默认流（Stream 0）是 CUDA 中最常用的流，但它的行为在不同 CUDA 版本中有变化。本节深入默认流的语义、Legacy vs Per-thread 模式，以及同步行为。

### 4.1 默认流的定义

**Legacy Default Stream**（CUDA 7.0 之前）：
- 所有线程共享同一个默认流
- 默认流与所有非默认流同步

**Per-thread Default Stream**（CUDA 7.0+）：
- 每个线程有独立的默认流
- 默认流不与非默认流同步

### 4.2 Legacy Default Stream 的行为

**同步语义**：

```c
// 线程 1
kernel1<<<blocks, threads, 0, stream1>>>(args1);
kernel2<<<blocks, threads>>>(args2);  // 默认流

// 线程 2
kernel3<<<blocks, threads, 0, stream2>>>(args3);
kernel4<<<blocks, threads>>>(args4);  // 默认流
```

**执行顺序**：
1. `kernel1` 在 `stream1` 中启动
2. `kernel2` 在默认流中启动，**等待 `stream1` 和 `stream2` 完成**
3. `kernel3` 在 `stream2` 中启动
4. `kernel4` 在默认流中启动，**等待所有流完成**

**问题**：默认流是同步点，会阻塞所有其他流，降低并发性能。

### 4.3 Per-thread Default Stream 的行为

**启用方式**：

```bash
# 编译时指定
nvcc --default-stream per-thread program.cu -o program
```

**同步语义**：

```c
// 线程 1
kernel1<<<blocks, threads, 0, stream1>>>(args1);
kernel2<<<blocks, threads>>>(args2);  // 线程 1 的默认流

// 线程 2
kernel3<<<blocks, threads, 0, stream2>>>(args3);
kernel4<<<blocks, threads>>>(args4);  // 线程 2 的默认流
```

**执行顺序**：
1. `kernel1` 在 `stream1` 中启动
2. `kernel2` 在线程 1 的默认流中启动，**只等待线程 1 的流**
3. `kernel3` 在 `stream2` 中启动
4. `kernel4` 在线程 2 的默认流中启动，**只等待线程 2 的流**

**优势**：
- 每个线程的默认流独立，不会相互阻塞
- 提高多线程程序的并发性能
- 更符合直觉（每个线程有自己的默认流）

### 4.4 默认流与非默认流的同步

**Legacy 模式**：

```c
cudaStream_t stream;
cudaStreamCreate(&stream);

kernel1<<<blocks, threads, 0, stream>>>(args1);
kernel2<<<blocks, threads>>>(args2);  // 默认流，等待 stream 完成
kernel3<<<blocks, threads, 0, stream>>>(args3);  // 等待默认流完成
```

**执行顺序**：
1. `kernel1` 在 `stream` 中启动
2. `kernel2` 在默认流中启动，**等待 `stream` 完成**
3. `kernel3` 在 `stream` 中启动，**等待默认流完成**

**Per-thread 模式**：

```c
cudaStream_t stream;
cudaStreamCreate(&stream);

kernel1<<<blocks, threads, 0, stream>>>(args1);
kernel2<<<blocks, threads>>>(args2);  // 默认流，不等待 stream
kernel3<<<blocks, threads, 0, stream>>>(args3);  // 不等待默认流
```

**执行顺序**：
1. `kernel1` 在 `stream` 中启动
2. `kernel2` 在默认流中启动，**不等待 `stream`**
3. `kernel3` 在 `stream` 中启动，**不等待默认流**

**如何选择？**

| 场景 | 推荐模式 |
|------|----------|
| 单线程程序 | Legacy(向后兼容,默认) |
| 多线程程序 | Per-thread(避免线程间同步阻塞) |
| 需要显式同步 | Per-thread + Event |
| 与旧库交互 | Legacy(若库假设默认流是全局同步点) |

> 编译时通过 `nvcc --default-stream per-thread` 切换;宏 `CUDA_API_PER_THREAD_DEFAULT_STREAM` 控制头文件中的默认流解析。不指定时默认为 Legacy 模式。

> **核心要点**：默认流的行为在 CUDA 7.0 后发生了变化。Legacy 模式下默认流是全局同步点，Per-thread 模式下每个线程有独立的默认流。多线程程序建议使用 Per-thread 模式，避免不必要的同步。

***

## 5. 资源生命周期管理

> CUDA 中的资源（内存、模块、Stream 等）都有生命周期。本节深入资源的生命周期管理，以及 Runtime 的自动清理机制。

### 5.1 资源的生命周期

**典型生命周期**：

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
stateDiagram-v2
    [*] --> 创建: cudaMalloc/cudaStreamCreate
    创建 --> 使用中: 正常状态
    使用中 --> 使用中: 多次访问
    使用中 --> 销毁: cudaFree/cudaStreamDestroy
    销毁 --> [*]
    
    note right of 创建
        分配资源
        初始化状态
    end note
    
    note right of 使用中
        可以读写
        可以传递给 Kernel
    end note
    
    note right of 销毁
        释放资源
        指针失效
    end note
```

### 5.2 引用计数机制

**主上下文引用计数**(由 Driver 自动管理,CUDA 中只有 primary context 提供引用计数 API):

```c
// 保留主上下文(引用计数 +1)
CUcontext primary;
cuDevicePrimaryCtxRetain(&primary, device);

// 释放主上下文引用(引用计数 -1)
cuDevicePrimaryCtxRelease(device);

// 引用计数归 0 时,主上下文仍保留,直到调用 cuDevicePrimaryCtxReset 才销毁
// 这与 cuCtxDestroy(显式上下文)不同
```

> 注意:CUDA Driver API 中**没有** `cuCtxRetain`/`cuCtxRelease` 这两个函数(常见误解)。引用计数机制只针对 primary context,通过 `cuDevicePrimaryCtxRetain`/`cuDevicePrimaryCtxRelease` 管理;显式创建的上下文(`cuCtxCreate`)只能通过 `cuCtxDestroy` 销毁,无 retain/release 计数。

**模块引用计数**：

```c
// 加载模块（引用计数 = 1）
CUmodule module;
cuModuleLoad(&module, "kernel.cubin");

// 卸载模块（引用计数 = 0，实际卸载）
cuModuleUnload(module);
```

### 5.3 进程退出时的清理

**Runtime 的自动清理**：

进程退出时，Runtime 会自动清理所有资源：
1. 同步所有 Stream
2. 销毁所有上下文
3. 释放所有设备内存
4. 卸载所有模块

**具体流程**（伪代码）：

```c
__attribute__((destructor))
void __cuda_runtime_cleanup(void) {
    // 1. 同步所有 Stream
    cudaDeviceSynchronize();
    
    // 2. 遍历所有上下文
    for (each context in context_list) {
        // 3. 释放上下文中的资源
        for (each allocation in context.allocations) {
            cuMemFree(allocation.ptr);
        }
        
        // 4. 卸载模块
        for (each module in context.modules) {
            cuModuleUnload(module);
        }
        
        // 5. 销毁上下文
        cuCtxDestroy(context);
    }
}
```

**注意**：
- 自动清理可能掩盖资源泄漏问题
- 建议在程序退出前显式释放资源
- 使用 `compute-sanitizer` 检测资源泄漏

### 5.4 资源泄漏检测

**工具**：`compute-sanitizer`

```bash
# 检测内存泄漏
compute-sanitizer --tool memcheck ./program

# 检测资源泄漏
compute-sanitizer --tool leakcheck ./program
```

**常见泄漏场景**：
- 忘记 `cudaFree`
- 忘记 `cudaStreamDestroy`
- 忘记 `cudaEventDestroy`
- 上下文未正确销毁

**最佳实践**：

```c
// 好：使用 RAII 模式管理资源
class CudaMemory {
    void *ptr;
public:
    CudaMemory(size_t size) {
        cudaMalloc(&ptr, size);
    }
    ~CudaMemory() {
        cudaFree(ptr);
    }
    void *get() { return ptr; }
};

// 使用
{
    CudaMemory data(1000 * sizeof(float));
    // 使用 data.get()...
}  // 自动释放
```

> **核心要点**：CUDA 资源有明确的生命周期，Runtime 通过引用计数管理资源。进程退出时 Runtime 会自动清理，但建议显式管理资源，避免泄漏。

***

## 6. Runtime 的设计决策

> 理解了 Runtime 的内部机制后，本节从系统软件工程师的视角，分析 Runtime 的设计决策及其权衡。

### 6.1 为什么选择延迟初始化？

**设计动机**：
- **简化编程**：用户无需手动初始化 CUDA
- **按需加载**：首次使用时才创建资源，减少启动开销
- **错误延迟**：初始化错误在首次使用时才暴露，便于调试

**权衡**：
- **优点**：编程简单，启动快
- **缺点**：首次调用延迟大（约 100-500 毫秒），不适合实时应用

**具体例子**：

```c
// 首次调用触发初始化
cudaMalloc((void **)&d_data, size);  // 耗时 100-500ms

// 后续调用快速
cudaMalloc((void **)&d_data2, size);  // 耗时 < 1ms
```

### 6.2 为什么需要主上下文？

**设计动机**：
- **简化多设备编程**：每个设备一个主上下文，自动管理
- **避免上下文切换**：主上下文是默认上下文，无需手动切换
- **资源隔离**：每个设备的主上下文独立，资源不冲突

**权衡**：
- **优点**：编程简单，适合单设备场景
- **缺点**：无法灵活控制上下文生命周期

### 6.3 为什么默认流是同步的？

**历史原因**：
- CUDA 早期只有默认流，为了简化语义，默认流与所有流同步
- 这样可以保证代码的正确性，避免数据竞争

**演进**：
- CUDA 7.0 引入 Per-thread Default Stream，解决多线程性能问题
- 但 Legacy 模式仍然是默认，保持向后兼容

**权衡**：
- **Legacy 模式**：正确性优先，性能较差
- **Per-thread 模式**：性能优先，需要手动同步

### 6.4 Runtime vs Driver 的分层动机

**设计动机**：
- **职责分离**：Runtime 负责易用性，Driver 负责灵活性
- **版本独立**：Runtime 可以独立于 Driver 升级
- **多语言支持**：Runtime 提供 C/C++ API，Driver 提供更底层的接口

**权衡**：
- **优点**：应用开发者使用 Runtime，系统软件工程师使用 Driver
- **缺点**：两层 API 增加学习成本，部分功能重复

> **核心要点**：Runtime 的设计决策体现了"易用性优先"的原则。延迟初始化、主上下文、默认流同步等设计，都是为了简化编程。但这些设计也有代价（如首次调用延迟、同步开销），系统软件工程师需要理解这些权衡，在必要时使用 Driver API。

***

## 参考资料

- [CUDA Runtime API Reference](https://docs.nvidia.com/cuda/cuda-runtime-api/) — 参考了上下文管理、模块管理、Stream 语义
- [CUDA Driver API Reference](https://docs.nvidia.com/cuda/cuda-driver-api/) — 参考了底层 API 的语义
- [CUDA C Programming Guide §4.3. CUDA Runtime](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#cuda-runtime) — 参考了 Runtime 的设计

***

**上一篇**：[04-执行模型与同步机制](./04-执行模型与同步机制.md)
**下一篇**：[06-CUDA-Driver接口与实现](./06-CUDA-Driver接口与实现.md) — 深入 Driver API 的语义、线程安全性、以及高级场景
