# Zephyr RTOS 学习笔记

> 📖 **返回**：[学习文档导航](./README.md) | **深入专题**：[核心数据结构](./Zephyr_核心数据结构设计详解.md) | [线程状态迁移](./Zephyr_线程状态迁移详解.md)

## 目录
1. [Zephyr RTOS 简介](#1-zephyr-rtos-简介)
2. [系统架构](#2-系统架构)
3. [内核核心组件](#3-内核核心组件)
4. [线程管理](#4-线程管理)
5. [调度机制](#5-调度机制)
6. [同步机制](#6-同步机制)
7. [设备驱动模型](#7-设备驱动模型)
8. [子系统与服务](#8-子系统与服务)
9. [构建系统](#9-构建系统)
10. [开发环境搭建](#10-开发环境搭建)
11. [实战示例](#11-实战示例)
12. [学习资源](#12-学习资源)

---

## 1. Zephyr RTOS 简介

### 1.1 什么是 Zephyr RTOS？

Zephyr RTOS 是一个由 **Linux 基金会**托管的开源实时操作系统（RTOS），专为资源受限的嵌入式设备和物联网应用设计。它具有以下特点：

- **轻量级**：内核设计紧凑，最小可运行在 8KB RAM 的设备上
- **可扩展**：支持从简单传感器到复杂网关的各种应用场景
- **安全性**：内置多种安全机制，包括内存保护、用户空间隔离等
- **开源免费**：采用 Apache 2.0 许可证，商用免费

### 1.2 历史背景

> **背景知识**：Zephyr 最初由风河公司（Wind River，后被 Intel 收购）开发，其名称来源于风河公司的 VxWorks 产品线中的微内核技术。2016年，Intel 将其贡献给 Linux 基金会，成为开源项目。

### 1.3 主要特性

根据源码和官方文档，Zephyr 提供以下核心特性：

| 特性类别 | 具体功能 |
|---------|---------|
| **多线程服务** | 协作式、抢占式、时间片轮转调度 |
| **中断服务** | 编译时注册中断处理程序 |
| **内存管理** | 固定大小/可变大小内存块分配 |
| **线程同步** | 信号量、互斥锁、条件变量 |
| **数据传递** | 消息队列、管道、邮箱 |
| **电源管理** | 系统级和设备级电源管理 |
| **网络支持** | 完整的 TCP/IP 协议栈、蓝牙 5.0、OpenThread |
| **文件系统** | LittleFS、FatFS、ext2 支持 |

### 1.4 支持的架构

Zephyr 支持多种 CPU 架构，源码位于 `/arch/` 目录：

```
arch/
├── arc/          # Synopsys ARC 架构
├── arm/          # ARM Cortex-M/A/R 系列
├── x86/          # Intel x86 (32/64位)
├── riscv/        # RISC-V 架构
├── xtensa/       # Tensilica Xtensa
├── sparc/        # SPARC V8
├── mips/         # MIPS 架构
└── openrisc/     # OpenRISC 架构
```

---

## 2. 系统架构

### 2.1 微内核架构

> **技术背景**：微内核架构（Microkernel Architecture）是一种操作系统设计理念，将系统功能最小化到内核中，其他服务以独立进程形式运行在用户空间。相比单内核（Monolithic Kernel），微内核具有更好的模块化和安全性。

Zephyr 采用**微内核架构**设计，具有以下特点：

```
┌─────────────────────────────────────────────────────────┐
│                    应用程序层                             │
├─────────────────────────────────────────────────────────┤
│                    子系统层                               │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐   │
│  │ 网络    │ 蓝牙    │ 文件系统 │ 电源管理 │ 日志    │   │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘   │
├─────────────────────────────────────────────────────────┤
│                    内核服务层                             │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐   │
│  │ 线程管理 │ 调度器  │ 内存管理 │ 同步机制 │ 定时器  │   │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘   │
├─────────────────────────────────────────────────────────┤
│                    设备驱动层                             │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐   │
│  │ GPIO    │ UART    │ SPI     │ I2C     │ Flash   │   │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘   │
├─────────────────────────────────────────────────────────┤
│                    硬件抽象层 (HAL)                       │
├─────────────────────────────────────────────────────────┤
│                    硬件平台                               │
│  ARM Cortex-M | RISC-V | x86 | ARC | Xtensa            │
└─────────────────────────────────────────────────────────┘
```

### 2.2 源码目录结构

```
zephyr/
├── arch/              # 架构相关代码
│   ├── arm/           # ARM 架构支持
│   ├── x86/           # x86 架构支持
│   └── ...
├── kernel/            # 内核核心代码
│   ├── sched.c        # 调度器
│   ├── thread.c       # 线程管理
│   ├── mutex.c        # 互斥锁
│   ├── sem.c          # 信号量
│   ├── timer.c        # 定时器
│   ├── timeout.c      # 超时处理
│   ├── init.c         # 内核初始化
│   └── ...
├── drivers/           # 设备驱动
│   ├── gpio/          # GPIO 驱动
│   ├── serial/        # 串口驱动
│   ├── spi/           # SPI 驱动
│   └── ...
├── subsys/            # 子系统
│   ├── bluetooth/     # 蓝牙子系统
│   ├── net/           # 网络子系统
│   ├── fs/            # 文件系统
│   └── ...
├── lib/               # 库函数
│   ├── libc/          # C 库
│   ├── os/            # 操作系统服务
│   └── utils/         # 工具函数
├── soc/               # SoC 支持
├── boards/            # 开发板定义
├── include/           # 头文件
└── samples/           # 示例代码
```

---

## 3. 内核核心组件

### 3.1 内核初始化流程

内核初始化代码位于 [kernel/init.c](file:///home/pbw/rtos/zephyr/kernel/init.c)，初始化分为多个级别：

```c
enum init_level {
    INIT_LEVEL_EARLY = 0,        // 早期初始化
    INIT_LEVEL_PRE_KERNEL_1,     // 内核初始化前阶段1
    INIT_LEVEL_PRE_KERNEL_2,     // 内核初始化前阶段2
    INIT_LEVEL_POST_KERNEL,      // 内核初始化后
    INIT_LEVEL_APPLICATION,      // 应用程序初始化
#ifdef CONFIG_SMP
    INIT_LEVEL_SMP,              // SMP 多核初始化
#endif
};
```

**初始化顺序说明**：

1. **EARLY**：最基本的硬件初始化（如时钟配置）
2. **PRE_KERNEL_1**：内核服务启动前的第一阶段
3. **PRE_KERNEL_2**：内核服务启动前的第二阶段
4. **POST_KERNEL**：内核核心服务已可用
5. **APPLICATION**：应用程序级初始化
6. **SMP**：多核处理器相关初始化

### 3.2 内核配置系统（Kconfig）

Zephyr 使用 Kconfig 系统进行内核配置，主配置文件为 [Kconfig.zephyr](file:///home/pbw/rtos/zephyr/Kconfig.zephyr)：

```kconfig
# 示例：线程优先级配置
config NUM_COOP_PRIORITIES
    int "Number of coop priorities"
    default 16
    range 0 128
    help
      Number of cooperative priorities configured in the system.

config NUM_PREEMPT_PRIORITIES
    int "Number of preemptible priorities"
    default 15
    range 0 127
    help
      Number of preemptible priorities available in the system.
```

> **知识点**：Kconfig 是 Linux 内核使用的配置系统，允许开发者通过菜单或配置文件选择需要的功能模块，实现内核的裁剪和定制。

### 3.3 内核对象

Zephyr 中的内核对象包括：

| 对象类型 | 用途 | 源码位置 |
|---------|------|---------|
| `k_thread` | 线程 | [thread.c](file:///home/pbw/rtos/zephyr/kernel/thread.c) |
| `k_mutex` | 互斥锁 | [mutex.c](file:///home/pbw/rtos/zephyr/kernel/mutex.c) |
| `k_sem` | 信号量 | [sem.c](file:///home/pbw/rtos/zephyr/kernel/sem.c) |
| `k_timer` | 定时器 | [timer.c](file:///home/pbw/rtos/zephyr/kernel/timer.c) |
| `k_pipe` | 管道 | [pipe.c](file:///home/pbw/rtos/zephyr/kernel/pipe.c) |
| `k_queue` | 队列 | [queue.c](file:///home/pbw/rtos/zephyr/kernel/queue.c) |

---

## 4. 线程管理

> 📖 **深入阅读**：线程相关的数据结构设计详见 [Zephyr_核心数据结构设计详解.md](./Zephyr_核心数据结构设计详解.md)

### 4.1 线程基础概念

> **背景知识**：线程是操作系统中最小的执行单元，是 CPU 调度的基本单位。在 RTOS 中，线程通常也被称为"任务"（Task）。每个线程有自己的栈空间、优先级和状态。

### 4.2 线程结构体

线程控制块定义了线程的所有属性：

```c
struct k_thread {
    struct _thread_base base;     // 基础信息（优先级、状态等）
    
    void *custom_data;            // 用户自定义数据
    struct _thread_stack_info stack_info;  // 栈信息
    
#ifdef CONFIG_THREAD_NAME
    char name[CONFIG_THREAD_MAX_NAME_LEN];  // 线程名称
#endif
    
    struct k_mem_domain *mem_domain;  // 内存域
    
    // ... 其他字段
};
```

### 4.3 线程状态

Zephyr 的线程状态使用**位标志（Bit Flags）**表示，线程可以同时处于多个状态。

> 📖 **详细的线程状态定义、状态迁移图、状态判断函数、API 对照表请参考**：[Zephyr_线程状态迁移详解.md](./Zephyr_线程状态迁移详解.md)

#### 状态标志概览

```c
#define _THREAD_DUMMY     (BIT(0))   // 虚拟线程
#define _THREAD_PENDING   (BIT(1))   // 线程正在等待对象
#define _THREAD_SLEEPING  (BIT(2))   // 线程正在睡眠
#define _THREAD_DEAD      (BIT(3))   // 线程已终止
#define _THREAD_SUSPENDED (BIT(4))   // 线程被挂起
#define _THREAD_QUEUED    (BIT(7))   // 线程在就绪队列中
```

#### 状态分类

| 分类 | 状态标志 | 说明 |
|-----|---------|------|
| **可运行** | `_QUEUED` | 在就绪队列中，等待调度 |
| **阻塞** | `_PENDING`, `_SLEEPING`, `_SUSPENDED` | 等待资源、睡眠、被挂起 |
| **终止** | `_DEAD` | 线程已结束 |

#### 状态迁移简图

```
  创建 ──► SLEEPING ──(k_thread_start)──► READY ◄──► RUNNING
                                                    │
                    ┌───────────────────────────────┼───────────────────┐
                    │                               │                   │
                    ▼                               ▼                   ▼
               PENDING                         SLEEPING            SUSPENDED
              (等待资源)                        (睡眠)              (被挂起)
                    │                               │                   │
                    └───────────────────────────────┴───────────────────┘
                                            │
                                            ▼
                                          DEAD (终止)
```

### 4.4 线程 API

```c
// 创建线程
k_tid_t k_thread_create(struct k_thread *new_thread,
                        k_thread_stack_t *stack,
                        size_t stack_size,
                        k_thread_entry_t entry,
                        void *p1, void *p2, void *p3,
                        int prio, uint32_t options, k_timeout_t delay);

// 启动线程
void k_thread_start(k_tid_t thread);

// 挂起线程
void k_thread_suspend(k_tid_t thread);

// 恢复线程
void k_thread_resume(k_tid_t thread);

// 终止线程
void k_thread_abort(k_tid_t thread);

// 获取当前线程
k_tid_t k_current_get(void);

// 设置线程优先级
void k_thread_priority_set(k_tid_t thread, int prio);

// 线程睡眠
void k_sleep(k_timeout_t timeout);
void k_msleep(int32_t ms);
void k_usleep(int32_t us);
```

### 4.5 线程优先级

Zephyr 使用**数值越小优先级越高**的规则：

```c
// 协作式线程优先级（负数）
K_PRIO_COOP(n)  // 定义协作式优先级，n = 0 为最高

// 抢占式线程优先级（非负数）
K_PRIO_PREEMPT(n)  // 定义抢占式优先级，n = 0 为最高

// 示例
#define THREAD_PRIORITY_HIGH   K_PRIO_PREEMPT(0)   // 最高抢占优先级
#define THREAD_PRIORITY_NORMAL K_PRIO_PREEMPT(7)   // 普通优先级
#define THREAD_PRIORITY_LOW    K_PRIO_PREEMPT(15)  // 最低抢占优先级
```

### 4.6 线程栈管理

```c
// 定义线程栈
K_THREAD_STACK_DEFINE(my_stack, STACK_SIZE);

// 使用示例
K_THREAD_STACK_DEFINE(my_thread_stack, 1024);
struct k_thread my_thread_data;

k_thread_create(&my_thread_data, my_thread_stack,
                K_THREAD_STACK_SIZEOF(my_thread_stack),
                my_thread_entry,
                NULL, NULL, NULL,
                THREAD_PRIORITY, 0, K_NO_WAIT);
```

---

## 5. 调度机制

### 5.1 调度器概述

调度器是 RTOS 的核心组件，负责决定哪个线程获得 CPU 使用权。Zephyr 的调度器代码位于 [kernel/sched.c](file:///home/pbw/rtos/zephyr/kernel/sched.c)。

### 5.2 调度策略

Zephyr 支持多种调度策略：

#### 5.2.1 协作式调度（Cooperative Scheduling）

```c
// 协作式线程：不会被抢占，除非主动让出 CPU
K_THREAD_DEFINE(coop_thread, STACK_SIZE,
                coop_thread_entry, NULL, NULL, NULL,
                K_PRIO_COOP(0),  // 负数优先级
                0, 0);
```

**特点**：
- 线程主动放弃 CPU（调用 `k_yield()`、`k_sleep()` 或等待同步对象）
- 响应时间可预测
- 适合实时性要求高的任务

#### 5.2.2 抢占式调度（Preemptive Scheduling）

```c
// 抢占式线程：可被更高优先级的线程抢占
K_THREAD_DEFINE(preempt_thread, STACK_SIZE,
                preempt_thread_entry, NULL, NULL, NULL,
                K_PRIO_PREEMPT(5),  // 非负优先级
                0, 0);
```

**特点**：
- 高优先级线程就绪时立即抢占低优先级线程
- 响应速度快
- 需要注意共享资源的保护

#### 5.2.3 时间片轮转（Time Slicing）

```c
// 在 prj.conf 中启用
CONFIG_TIMESLICING=y
CONFIG_TIMESLICE_SIZE=5000    // 时间片大小（毫秒）
CONFIG_TIMESLICE_PRIORITY=0   // 参与时间片的最低优先级
```

**特点**：
- 同优先级的线程轮流执行
- 每个线程运行固定时间片后切换
- 适合需要公平调度的场景

#### 5.2.4 最早截止时间优先（EDF）

```c
// 启用 EDF 调度
CONFIG_SCHED_DEADLINE=y
```

**特点**：
- 根据任务的截止时间调度
- 截止时间最近的任务优先执行
- 适合周期性实时任务

### 5.3 调度器核心数据结构

```c
// 就绪队列
struct _ready_q {
    struct k_thread *cache;     // 缓存当前最高优先级线程
#if defined(CONFIG_SCHED_DUMB)
    sys_dlist_t runq;           // 简单链表实现
#elif defined(CONFIG_SCHED_SCALABLE)
    struct _priq_rb runq;       // 红黑树实现
#elif defined(CONFIG_SCHED_MULTIQ)
    struct _priq_mq runq;       // 多队列实现
#endif
};
```

### 5.4 调度器 API

```c
// 让出 CPU
void k_yield(void);

// 锁定调度器（禁止调度）
k_sched_lock();

// 解锁调度器
k_sched_unlock();

// 检查是否在 ISR 中
bool k_is_in_isr(void);

// 检查当前线程是否可被抢占
int k_is_preempt_thread(void);
```

### 5.5 SMP 多核调度

Zephyr 支持 SMP（对称多处理）系统：

```c
// 启用 SMP 支持
CONFIG_SMP=y
CONFIG_MP_MAX_NUM_CPUS=2  // CPU 核心数

// CPU 亲和性设置
void k_thread_cpu_pin(k_tid_t thread, int cpu);
int k_thread_cpu_mask_clear(k_tid_t thread);
int k_thread_cpu_mask_enable_all(k_tid_t thread);
int k_thread_cpu_mask_enable(k_tid_t thread, int cpu);
int k_thread_cpu_mask_disable(k_tid_t thread, int cpu);
```

---

## 6. 同步机制

### 6.1 信号量（Semaphore）

信号量源码位于 [kernel/sem.c](file:///home/pbw/rtos/zephyr/kernel/sem.c)。

> **背景知识**：信号量由荷兰计算机科学家 Dijkstra 提出，是一种用于控制多个进程/线程对共享资源访问的同步机制。信号量维护一个计数器，表示可用资源的数量。

#### 6.1.1 信号量类型

- **二进制信号量**：计数为 0 或 1，用于互斥访问
- **计数信号量**：计数可为任意非负整数，用于资源池管理

#### 6.1.2 信号量 API

```c
// 定义和初始化信号量
struct k_sem my_sem;
k_sem_init(&my_sem, initial_count, limit);

// 或使用宏定义
K_SEM_DEFINE(my_sem, 0, 1);  // 初始值0，最大值1

// 获取信号量（P操作/Wait）
int k_sem_take(struct k_sem *sem, k_timeout_t timeout);

// 释放信号量（V操作/Signal）
void k_sem_give(struct k_sem *sem);

// 获取当前计数
unsigned int k_sem_count_get(struct k_sem *sem);
```

#### 6.1.3 使用示例

```c
K_SEM_DEFINE(my_sem, 0, 1);  // 二进制信号量，初始不可用

// 生产者线程
void producer_thread(void)
{
    while (1) {
        // 生产数据
        produce_data();
        
        // 通知消费者
        k_sem_give(&my_sem);
        
        k_msleep(100);
    }
}

// 消费者线程
void consumer_thread(void)
{
    while (1) {
        // 等待数据
        k_sem_take(&my_sem, K_FOREVER);
        
        // 消费数据
        consume_data();
    }
}
```

### 6.2 互斥锁（Mutex）

互斥锁源码位于 [kernel/mutex.c](file:///home/pbw/rtos/zephyr/kernel/mutex.c)。

> **背景知识**：互斥锁（Mutex，Mutual Exclusion 的缩写）是一种特殊的二进制信号量，具有**优先级继承**特性，可以解决优先级反转问题。

#### 6.2.1 优先级继承

当高优先级线程等待低优先级线程持有的互斥锁时，低优先级线程会临时提升到高优先级，以尽快释放锁。

```c
// 源码中的优先级继承实现（mutex.c）
static bool adjust_owner_prio(struct k_mutex *mutex, int32_t new_prio)
{
    if (mutex->owner->base.prio != new_prio) {
        // 调整持有者优先级
        LOG_DBG("%p prio changed to %d (was %d)",
                mutex->owner, new_prio, mutex->owner->base.prio);
        // ...
    }
}
```

#### 6.2.2 互斥锁 API

```c
// 定义和初始化互斥锁
struct k_mutex my_mutex;
k_mutex_init(&my_mutex);

// 或使用宏定义
K_MUTEX_DEFINE(my_mutex);

// 锁定互斥锁
int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout);

// 解锁互斥锁
int k_mutex_unlock(struct k_mutex *mutex);
```

#### 6.2.3 使用示例

```c
K_MUTEX_DEFINE(my_mutex);

void protected_function(void)
{
    // 锁定互斥锁
    k_mutex_lock(&my_mutex, K_FOREVER);
    
    // 访问共享资源
    access_shared_resource();
    
    // 解锁互斥锁
    k_mutex_unlock(&my_mutex);
}
```

### 6.3 条件变量（Condition Variable）

```c
// 定义条件变量
K_CONDVAR_DEFINE(my_condvar);
K_MUTEX_DEFINE(my_mutex);

// 等待条件
k_mutex_lock(&my_mutex, K_FOREVER);
while (!condition_is_true()) {
    k_condvar_wait(&my_condvar, &my_mutex, K_FOREVER);
}
// 处理条件满足后的逻辑
k_mutex_unlock(&my_mutex);

// 发送信号
k_mutex_lock(&my_mutex, K_FOREVER);
// 改变条件
change_condition();
k_condvar_signal(&my_condvar);
k_mutex_unlock(&my_mutex);
```

### 6.4 事件（Events）

```c
// 定义事件
K_EVENT_DEFINE(my_event);

// 等待事件
uint32_t events = k_event_wait(&my_event, EVENT_MASK, 
                                false, K_FOREVER);

// 发送事件
k_event_post(&my_event, EVENT_BIT);
```

### 6.5 同步机制对比

| 机制 | 用途 | 特点 |
|-----|------|------|
| 信号量 | 资源计数、同步 | 无优先级继承 |
| 互斥锁 | 互斥访问 | 有优先级继承 |
| 条件变量 | 条件等待 | 需配合互斥锁使用 |
| 事件 | 多条件等待 | 支持位掩码 |

---

## 7. 设备驱动模型

### 7.1 驱动模型概述

Zephyr 采用统一的设备驱动模型，驱动代码位于 `/drivers/` 目录。设备初始化代码位于 [kernel/device.c](file:///home/pbw/rtos/zephyr/kernel/device.c)。

### 7.2 设备结构体

```c
struct device {
    const char *name;                   // 设备名称
    const void *config;                 // 配置信息（只读）
    const void *api;                    // API 函数指针表
    struct device_state *state;         // 运行时状态
    void *data;                         // 设备私有数据
    // ...
};
```

### 7.3 设备初始化

```c
// 定义设备驱动
DEVICE_DEFINE(my_device,           // 设备名称
              "my_dev",             // 字符串名称
              my_device_init,       // 初始化函数
              NULL,                 // PM 设备操作（可选）
              &my_dev_data,         // 设备数据
              &my_dev_config,       // 设备配置
              POST_KERNEL,          // 初始化级别
              CONFIG_MY_DEV_INIT_PRIORITY,  // 初始化优先级
              &my_dev_api);         // API 表

// 简化宏
DEVICE_DT_DEFINE(node_id, init_fn, pm_device, 
                 data_ptr, cfg_ptr, level, prio, api_ptr);
```

### 7.4 设备树（Devicetree）

> **背景知识**：设备树（Devicetree）是一种描述硬件的数据结构，最初用于 PowerPC 系统，后被 Linux 内核广泛采用。Zephyr 使用设备树来描述硬件配置，实现硬件描述与代码分离。

#### 7.4.1 设备树文件示例

```dts
/ {
    chosen {
        zephyr,console = &uart0;
        zephyr,shell-uart = &uart0;
    };
    
    soc {
        uart0: uart@4000e000 {
            compatible = "vendor,uart";
            reg = <0x4000e000 0x1000>;
            interrupts = <1 0>;
            status = "okay";
        };
    };
};
```

#### 7.4.2 在代码中使用设备树

```c
// 获取设备树节点
#define MY_DEV_NODE DT_NODELABEL(uart0)

// 获取设备实例
static const struct device *my_dev = DEVICE_DT_GET(MY_DEV_NODE);

// 检查设备是否就绪
if (!device_is_ready(my_dev)) {
    printk("Device not ready\n");
    return;
}
```

### 7.5 驱动分类

根据 `/drivers/Kconfig`，Zephyr 支持丰富的驱动类型：

```
drivers/
├── adc/           # ADC 模数转换
├── bluetooth/     # 蓝牙
├── can/           # CAN 总线
├── clock_control/ # 时钟控制
├── console/       # 控制台
├── counter/       # 计数器/RTC
├── crypto/        # 加密硬件
├── dac/           # DAC 数模转换
├── display/       # 显示驱动
├── dma/           # DMA 控制器
├── eeprom/        # EEPROM
├── entropy/       # 随机数生成器
├── ethernet/      # 以太网
├── flash/         # Flash 存储
├── gpio/          # GPIO
├── i2c/           # I2C 总线
├── i2s/           # I2S 音频接口
├── ieee802154/    # IEEE 802.15.4 无线
├── interrupt_controller/  # 中断控制器
├── led/           # LED 驱动
├── pwm/           # PWM
├── sensor/        # 传感器
├── serial/        # 串口
├── spi/           # SPI 总线
├── timer/         # 定时器
├── usb/           # USB
├── watchdog/      # 看门狗
└── wifi/          # WiFi
```

### 7.6 编写自定义驱动

```c
// 1. 定义 API 结构
struct my_driver_api {
    void (*enable)(const struct device *dev);
    void (*disable)(const struct device *dev);
    int (*read)(const struct device *dev, uint8_t *data, size_t len);
};

// 2. 实现驱动函数
static void my_driver_enable(const struct device *dev)
{
    struct my_dev_data *data = dev->data;
    // 实现启用逻辑
}

static int my_driver_read(const struct device *dev, uint8_t *data, size_t len)
{
    // 实现读取逻辑
    return 0;
}

// 3. 定义 API 表
static const struct my_driver_api my_api = {
    .enable = my_driver_enable,
    .disable = my_driver_disable,
    .read = my_driver_read,
};

// 4. 初始化函数
static int my_driver_init(const struct device *dev)
{
    // 初始化硬件
    return 0;
}

// 5. 定义设备
DEVICE_DT_INST_DEFINE(0, my_driver_init, NULL,
                      &my_data, &my_config,
                      POST_KERNEL, CONFIG_MY_DRIVER_INIT_PRIORITY,
                      &my_api);
```

---

## 8. 子系统与服务

### 8.1 子系统概述

Zephyr 的子系统位于 `/subsys/` 目录，提供高级功能服务。

### 8.2 主要子系统

#### 8.2.1 蓝牙子系统

```c
// 启用蓝牙
CONFIG_BT=y
CONFIG_BT_PERIPHERAL=y
CONFIG_BT_DEVICE_NAME="My Device"

// 代码示例
#include <zephyr/bluetooth/bluetooth.h>

void main(void)
{
    int err = bt_enable(NULL);
    if (err) {
        printk("Bluetooth init failed\n");
        return;
    }
    printk("Bluetooth initialized\n");
}
```

#### 8.2.2 网络子系统

```c
// 启用网络
CONFIG_NETWORKING=y
CONFIG_NET_IPV4=y
CONFIG_NET_TCP=y

// Socket 编程示例
#include <zephyr/net/socket.h>

int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
struct sockaddr_in addr;
addr.sin_family = AF_INET;
addr.sin_port = htons(8080);
inet_pton(AF_INET, "192.168.1.1", &addr.sin_addr);

connect(sock, (struct sockaddr *)&addr, sizeof(addr));
```

#### 8.2.3 文件系统子系统

```c
// 启用文件系统
CONFIG_FILE_SYSTEM=y
CONFIG_FAT_FILESYSTEM_ELM=y

// 文件操作
#include <zephyr/fs/fs.h>

struct fs_file_t file;
fs_open(&file, "/lfs/test.txt", FS_O_WRITE | FS_O_CREATE);
fs_write(&file, data, sizeof(data));
fs_close(&file);
```

#### 8.2.4 日志子系统

```c
// 配置日志
CONFIG_LOG=y
CONFIG_LOG_DEFAULT_LEVEL=3

// 代码中使用
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(my_module, CONFIG_MY_MODULE_LOG_LEVEL);

void my_function(void)
{
    LOG_INF("Information message");
    LOG_WRN("Warning message");
    LOG_ERR("Error message");
    LOG_DBG("Debug message");
}
```

#### 8.2.5 Shell 子系统

```c
// 启用 Shell
CONFIG_SHELL=y

// 注册 Shell 命令
#include <zephyr/shell/shell.h>

static int cmd_hello(const struct shell *sh, size_t argc, char **argv)
{
    shell_print(sh, "Hello, World!");
    return 0;
}

SHELL_CMD_REGISTER(hello, NULL, "Say hello", cmd_hello);
```

#### 8.2.6 电源管理子系统

```c
// 启用电源管理
CONFIG_PM=y
CONFIG_PM_DEVICE=y

// 设备电源管理
#include <zephyr/pm/device.h>

pm_device_action_run(dev, PM_DEVICE_ACTION_SUSPEND);
pm_device_action_run(dev, PM_DEVICE_ACTION_RESUME);
```

### 8.3 子系统配置

根据 `/subsys/Kconfig`，Zephyr 提供以下子系统：

| 子系统 | 功能描述 |
|-------|---------|
| bluetooth | 蓝牙协议栈 |
| net | TCP/IP 网络协议栈 |
| fs | 文件系统支持 |
| logging | 日志框架 |
| shell | 命令行接口 |
| pm | 电源管理 |
| usb | USB 协议栈 |
| settings | 设置存储 |
| random | 随机数生成 |
| mgmt | 设备管理（MCUmgr） |
| lorawan | LoRaWAN 协议 |
| canbus | CAN 总线 |

---

## 9. 构建系统

### 9.1 CMake 构建系统

Zephyr 使用 CMake 作为构建系统：

```bash
# 基本构建命令
west build -b <board_name> <source_dir>

# 示例：为 nRF52840 构建蓝牙示例
west build -b nrf52840dk/nrf52840 samples/bluetooth/peripheral

# 指定配置文件
west build -b nrf52840dk/nrf52840 -- -DCONF_FILE=prj_release.conf

# 清理构建
west build -t clean

# 烧录
west flash
```

### 9.2 项目配置文件

```ini
# prj.conf - 项目配置文件

# 内核配置
CONFIG_MULTITHREADING=y
CONFIG_NUM_COOP_PRIORITIES=16
CONFIG_NUM_PREEMPT_PRIORITIES=15

# 调试选项
CONFIG_DEBUG=y
CONFIG_LOG=y
CONFIG_LOG_DEFAULT_LEVEL=3

# 启用串口控制台
CONFIG_SERIAL=y
CONFIG_CONSOLE=y
CONFIG_UART_CONSOLE=y

# 启用 Shell
CONFIG_SHELL=y
CONFIG_KERNEL_SHELL=y
```

### 9.3 CMakeLists.txt

```cmake
# 最小 CMakeLists.txt
cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(my_application)

target_sources(app PRIVATE src/main.c)
```

### 9.4 Kconfig 配置

```kconfig
# Kconfig - 项目自定义配置

config MY_CUSTOM_FEATURE
    bool "Enable my custom feature"
    default n
    help
      Enable this to use the custom feature.

config MY_CUSTOM_BUFFER_SIZE
    int "Buffer size for custom feature"
    default 256
    depends on MY_CUSTOM_FEATURE
```

---

## 10. 开发环境搭建

### 10.1 系统要求

- Python 3.8+
- CMake 3.20+
- Git
- 编译工具链（GCC、LLVM 等）

### 10.2 安装步骤

```bash
# 1. 创建 Python 虚拟环境
python3 -m venv ~/zephyrproject/.venv
source ~/zephyrproject/.venv/bin/activate

# 2. 安装 west（Zephyr 的元工具）
pip install west

# 3. 获取 Zephyr 源码
west init ~/zephyrproject
cd ~/zephyrproject
west update

# 4. 安装 Python 依赖
pip install -r ~/zephyrproject/zephyr/scripts/requirements.txt

# 5. 安装工具链
# Ubuntu/Debian:
sudo apt install cmake ninja-build gperf \
    ccache dfu-util device-tree-compiler wget \
    python3-dev python3-pip python3-setuptools \
    cargo xz-utils file make gcc gcc-multilib \
    g++-multilib libsdl2-dev libmagic1

# 6. 下载 Zephyr SDK
cd ~
wget https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v0.16.5/zephyr-sdk-0.16.5_linux-x86_64.tar.xz
tar xvf zephyr-sdk-0.16.5_linux-x86_64.tar.xz
cd zephyr-sdk-0.16.5
./setup.sh

# 7. 设置环境变量
cd ~/zephyrproject/zephyr
source zephyr-env.sh
```

### 10.3 验证安装

```bash
# 编译 Hello World 示例
cd ~/zephyrproject/zephyr
west build -b qemu_x86 samples/hello_world
west build -t run
```

---

## 11. 实战示例

### 11.1 多线程示例

```c
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define STACK_SIZE 1024
#define THREAD_PRIORITY 5

K_THREAD_STACK_DEFINE(producer_stack, STACK_SIZE);
K_THREAD_STACK_DEFINE(consumer_stack, STACK_SIZE);

struct k_thread producer_data;
struct k_thread consumer_data;

K_SEM_DEFINE(data_sem, 0, 1);
K_MUTEX_DEFINE(data_mutex);

static int shared_data = 0;

void producer_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);
    
    while (1) {
        k_mutex_lock(&data_mutex, K_FOREVER);
        shared_data++;
        printk("Producer: data = %d\n", shared_data);
        k_mutex_unlock(&data_mutex);
        
        k_sem_give(&data_sem);
        k_msleep(1000);
    }
}

void consumer_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);
    
    while (1) {
        k_sem_take(&data_sem, K_FOREVER);
        
        k_mutex_lock(&data_mutex, K_FOREVER);
        printk("Consumer: data = %d\n", shared_data);
        k_mutex_unlock(&data_mutex);
    }
}

int main(void)
{
    printk("Multi-thread example starting...\n");
    
    k_thread_create(&producer_data, producer_stack,
                    K_THREAD_STACK_SIZEOF(producer_stack),
                    producer_thread, NULL, NULL, NULL,
                    THREAD_PRIORITY, 0, K_NO_WAIT);
    
    k_thread_create(&consumer_data, consumer_stack,
                    K_THREAD_STACK_SIZEOF(consumer_stack),
                    consumer_thread, NULL, NULL, NULL,
                    THREAD_PRIORITY + 1, 0, K_NO_WAIT);
    
    return 0;
}
```

### 11.2 定时器示例

```c
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

struct k_timer my_timer;

void timer_expiry_fn(struct k_timer *timer)
{
    printk("Timer expired!\n");
}

void timer_stop_fn(struct k_timer *timer)
{
    printk("Timer stopped!\n");
}

int main(void)
{
    k_timer_init(&my_timer, timer_expiry_fn, timer_stop_fn);
    
    // 启动周期性定时器（每1秒触发）
    k_timer_start(&my_timer, K_SECONDS(1), K_SECONDS(1));
    
    // 等待5秒
    k_sleep(K_SECONDS(5));
    
    // 停止定时器
    k_timer_stop(&my_timer);
    
    printk("Timer example completed\n");
    return 0;
}
```

### 11.3 工作队列示例

```c
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

K_THREAD_STACK_DEFINE(work_q_stack, 1024);
struct k_work_q my_work_q;

struct k_work my_work;

void work_handler(struct k_work *work)
{
    printk("Processing work item\n");
    k_msleep(100);  // 模拟耗时操作
    printk("Work item completed\n");
}

int main(void)
{
    // 初始化工作队列
    k_work_queue_start(&my_work_q, work_q_stack,
                       K_THREAD_STACK_SIZEOF(work_q_stack),
                       K_PRIO_PREEMPT(5), NULL);
    
    // 初始化工作项
    k_work_init(&my_work, work_handler);
    
    // 提交工作项
    k_work_submit_to(&my_work_q, &my_work);
    
    // 延迟提交（1秒后执行）
    k_work_schedule_for(&my_work_q, &my_work, K_SECONDS(1));
    
    return 0;
}
```

### 11.4 GPIO 示例

```c
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>

#define LED_NODE DT_ALIAS(led0)
#define BUTTON_NODE DT_ALIAS(sw0)

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED_NODE, gpios);
static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(BUTTON_NODE, gpios);

static struct gpio_callback button_cb_data;

void button_pressed(const struct device *dev, struct gpio_callback *cb,
                    uint32_t pins)
{
    gpio_pin_toggle_dt(&led);
    printk("Button pressed!\n");
}

int main(void)
{
    int ret;
    
    // 检查设备就绪
    if (!device_is_ready(led.port)) {
        return -1;
    }
    
    // 配置 LED
    ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) {
        return ret;
    }
    
    // 配置按钮
    ret = gpio_pin_configure_dt(&button, GPIO_INPUT);
    if (ret < 0) {
        return ret;
    }
    
    // 配置中断
    ret = gpio_pin_interrupt_configure_dt(&button, GPIO_INT_EDGE_TO_ACTIVE);
    if (ret < 0) {
        return ret;
    }
    
    // 注册回调
    gpio_init_callback(&button_cb_data, button_pressed, BIT(button.pin));
    gpio_add_callback(button.port, &button_cb_data);
    
    printk("GPIO example ready\n");
    
    while (1) {
        k_msleep(1000);
    }
    
    return 0;
}
```

---

## 12. 学习资源

### 12.1 官方资源

| 资源 | 链接 |
|-----|------|
| 官方文档 | https://docs.zephyrproject.org |
| 源码仓库 | https://github.com/zephyrproject-rtos/zephyr |
| 官方网站 | https://www.zephyrproject.org |
| API 参考 | https://docs.zephyrproject.org/latest/doxygen/html/index.html |

### 12.2 社区资源

| 资源 | 链接 |
|-----|------|
| Discord | https://chat.zephyrproject.org |
| 用户邮件列表 | users@lists.zephyrproject.org |
| 开发者邮件列表 | devel@lists.zephyrproject.org |
| GitHub Issues | https://github.com/zephyrproject-rtos/zephyr/issues |

### 12.3 学习路径建议

```
入门阶段
├── 1. 搭建开发环境
├── 2. 运行 Hello World 示例
├── 3. 学习线程基础
└── 4. 理解调度机制

进阶阶段
├── 1. 掌握同步机制（信号量、互斥锁）
├── 2. 学习设备驱动模型
├── 3. 理解设备树
└── 4. 使用定时器和工作队列

高级阶段
├── 1. 网络编程
├── 2. 蓝牙开发
├── 3. 电源管理
├── 4. 编写自定义驱动
└── 5. 贡献代码

专业领域
├── 1. 安全开发
├── 2. 实时性优化
├── 3. 多核 SMP 开发
└── 4. 特定芯片深度定制
```

### 12.4 推荐开发板

| 开发板 | 架构 | 特点 | 适合场景 |
|-------|------|------|---------|
| nRF52840 DK | ARM Cortex-M4 | 蓝牙 5.0 | 蓝牙开发 |
| STM32 Nucleo | ARM Cortex-M | 生态丰富 | 通用学习 |
| ESP32 | Xtensa | WiFi+蓝牙 | IoT 项目 |
| FRDM-K64F | ARM Cortex-M4 | 以太网 | 网络开发 |
| QEMU | x86/ARM | 虚拟环境 | 快速测试 |

---

## 附录 A：常用 Kconfig 选项速查

```ini
# 内核基础
CONFIG_MULTITHREADING=y          # 启用多线程
CONFIG_NUM_COOP_PRIORITIES=16    # 协作式优先级数量
CONFIG_NUM_PREEMPT_PRIORITIES=15 # 抢占式优先级数量

# 调度选项
CONFIG_TIMESLICING=y             # 启用时间片
CONFIG_TIMESLICE_SIZE=5000       # 时间片大小(ms)
CONFIG_SCHED_DEADLINE=y          # EDF 调度

# 内存管理
CONFIG_HEAP_MEM_POOL_SIZE=4096   # 堆内存大小
CONFIG_MAIN_STACK_SIZE=1024      # 主线程栈大小
CONFIG_IDLE_STACK_SIZE=256       # 空闲线程栈大小

# 调试选项
CONFIG_DEBUG=y                   # 调试模式
CONFIG_LOG=y                     # 日志系统
CONFIG_LOG_DEFAULT_LEVEL=3       # 日志级别
CONFIG_ASSERT=y                  # 断言
CONFIG_DEBUG_THREAD_INFO=y       # 线程调试信息

# 电源管理
CONFIG_PM=y                      # 电源管理
CONFIG_PM_DEVICE=y               # 设备电源管理

# 网络
CONFIG_NETWORKING=y              # 网络支持
CONFIG_NET_IPV4=y                # IPv4
CONFIG_NET_TCP=y                 # TCP
CONFIG_NET_UDP=y                 # UDP

# 蓝牙
CONFIG_BT=y                      # 蓝牙支持
CONFIG_BT_PERIPHERAL=y           # 外设角色
CONFIG_BT_CENTRAL=y              # 中心角色

# Shell
CONFIG_SHELL=y                   # Shell 支持
CONFIG_KERNEL_SHELL=y            # 内核 Shell 命令
```

---

## 附录 B：常见问题解答

### Q1: 如何选择线程优先级？

**A**: 遵循以下原则：
- 实时性要求高的任务使用协作式优先级（负数）
- 普通任务使用抢占式优先级（非负数）
- 避免过多高优先级线程导致低优先级线程饥饿

### Q2: 信号量和互斥锁如何选择？

**A**: 
- 需要优先级继承时使用互斥锁
- 用于资源计数时使用信号量
- 简单的同步可以使用二进制信号量

### Q3: 如何减小内存占用？

**A**:
- 调整栈大小（`CONFIG_MAIN_STACK_SIZE` 等）
- 禁用不需要的功能
- 使用 `CONFIG_SIZE_OPTIMIZATIONS`
- 启用链接时优化（LTO）

### Q4: 如何调试线程问题？

**A**:
- 使用 `CONFIG_DEBUG_THREAD_INFO`
- 通过 Shell 命令 `kernel threads` 查看线程状态
- 使用 `k_thread_name_set()` 为线程命名
- 启用日志记录

---

## 结语

Zephyr RTOS 是一个功能强大、设计精良的嵌入式实时操作系统。通过本笔记的学习，你应该已经掌握了：

1. Zephyr 的基本架构和设计理念
2. 内核核心组件的使用方法
3. 线程管理和调度机制
4. 同步机制的应用场景
5. 设备驱动模型的开发流程
6. 各种子系统的配置和使用

建议在学习过程中多动手实践，从简单的示例开始，逐步深入到复杂的项目开发。同时，积极参与社区讨论，关注官方文档的更新，不断提升自己的嵌入式开发能力。

---

*本笔记基于 Zephyr 源码和官方文档整理，版本信息请参考 [VERSION](file:///home/pbw/rtos/zephyr/VERSION) 文件。*
