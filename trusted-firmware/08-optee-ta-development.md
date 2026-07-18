# OP-TEE TA 开发实践

> 一句话概括:本文从 TA 的 5 个入口点出发,讲清 TA 属性文件、Client API 调用流程、Internal API 三大类能力(加密/存储/时间),最后用一个完整的密钥保管 TA 把所有知识点串起来。
> **工程师视角**:TA 开发的核心模式是"入口点 + 命令分发 + 安全 API"——CA 用 Client API 通过 SMC 把请求送进来,TA 在 `TA_InvokeCommandEntryPoint` 里 switch-case 分发,真正干活的是 Internal API。掌握这个模式后,任何 GP 兼容 TEE 上的 TA 开发都大同小异。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| TA | Trusted Application | 运行在 S-EL0 的可信应用 |
| CA | Client Application | 运行在 REE 的客户端应用 |
| GP | GlobalPlatform | TEE API 标准化组织 |
| UUID | Universally Unique Identifier | 通用唯一识别码,TA 的全局标识 |
| PTA | Pseudo TA | OP-TEE 内核态(S-EL1)伪 TA |
| HUK | Hardware Unique Key | 硬件唯一密钥,派生 TA 专属密钥的基础 |
| RPMB | Replay Protected Memory Block | eMMC/UFS 抗回滚存储分区 |
| REE FS | REE File System | OP-TEE 安全存储的 REE 后端(加密 + 哈希树) |
| AEAD | Authenticated Encryption with Associated Data | 带相关数据的认证加密,如 AES-GCM |
| GCM | Galois/Counter Mode | AES 的一种认证加密模式 |
| IV | Initialization Vector | 初始化向量,加密算法的随机化输入 |
| TEEC | TEE Client | GP Client API 的前缀(如 TEEC_OpenSession) |
| TA_DEV_KIT | TA Development Kit | OP-TEE 提供的 TA 开发包,含头文件和链接脚本 |
| tee-supplicant | — | REE 侧守护进程,代 TEE 完成 RPMB/REE FS 等操作 |

**前置阅读**:[07-optee-architecture.md](./07-optee-architecture.md) — 本文假设读者已了解 OP-TEE 内部架构、SMC 通信链路和共享内存机制。

---

## 1. TA 的结构

> 上一篇讲了 OP-TEE 内部架构与 CA/TA 通信链路,但还没说"TA 本身长什么样"。本章把 TA 拆成两部分:5 个固定入口点(C 调用约定)+ 1 个属性头(UUID、栈、堆、标志位)。这两部分是任何 TA 都必须有的"骨架"。

### 1.1 TA 的 5 个入口点

GP Internal Core API 规范定义了 TA 的 5 个入口点,由 TEE Core(即 OP-TEE Core)在特定时机调用。它们构成了 TA 的完整生命周期:

| 入口点 | 调用时机 | 典型用途 | 返回值含义 |
|--------|---------|----------|------------|
| `TA_CreateEntryPoint` | TA 实例首次创建时(一次) | 全局初始化、分配全局资源 | 非 SUCCESS 则实例创建失败 |
| `TA_OpenSessionEntryPoint` | 每个 CA `OpenSession` 时 | 鉴权(检查客户端身份)、分配会话上下文 | 非 SUCCESS 则会话打开失败 |
| `TA_InvokeCommandEntryPoint` | CA `InvokeCommand` 时 | 真正的业务逻辑入口,按 cmd 分发 | 业务结果返回给 CA |
| `TA_CloseSessionEntryPoint` | CA `CloseSession` 或断开时 | 释放会话上下文 | 无返回值(void) |
| `TA_DestroyEntryPoint` | 实例销毁时(最后一个 session 关闭后) | 释放全局资源 | 无返回值(void) |

这 5 个函数的声明在 [lib/libutee/include/tee_internal_api.h](./src/optee-src/lib/libutee/include/tee_internal_api.h) 中,GP 规范要求所有 TA 必须实现它们:

```c
/* 来源: src/optee-src/lib/libutee/include/tee_internal_api.h (节选) */
TEE_Result TA_EXPORT TA_CreateEntryPoint(void);
void        TA_EXPORT TA_DestroyEntryPoint(void);
TEE_Result TA_EXPORT TA_OpenSessionEntryPoint(uint32_t paramTypes,
                                              TEE_Param params[TEE_NUM_PARAMS],
                                              void **sessionContext);
void        TA_EXPORT TA_CloseSessionEntryPoint(void *sessionContext);
TEE_Result TA_EXPORT
TA_InvokeCommandEntryPoint(void *sessionContext, uint32_t commandID,
                           uint32_t paramTypes,
                           TEE_Param params[TEE_NUM_PARAMS]);
```

**为什么是 5 个,不是 3 个?** 把 `Create`/`Destroy` 与 `OpenSession`/`CloseSession` 分开,是为了区分"全局资源"和"会话资源"。考虑一个支付 TA:它可能有全局密钥(整个 TA 生命周期持有)和每会话上下文(每个客户端一份)。`Create` 时初始化全局密钥,`OpenSession` 时分配会话上下文(指针通过 `sessionContext` 传出),`InvokeCommand` 时用这个上下文处理业务,`CloseSession` 时释放上下文,`Destroy` 时清掉全局密钥。

**实例模式**:TA 的实例化策略由属性位控制,见 1.2 节。`TA_FLAG_SINGLE_INSTANCE` 表示全局单实例(所有 CA 共享同一个 TA 实例),`TA_FLAG_MULTI_SESSION` 表示一个实例可以有多个会话。

### 1.2 TA 属性文件:user_ta_header_defines.h

每个 TA 必须提供一个 `user_ta_header_defines.h`,定义 TA 的"身份证信息"。OP-TEE 的 [ta/user_ta_header.c](./src/optee-src/ta/user_ta_header.c) 模板会引用这些宏,生成 TA 镜像的头部:

```c
/* 来源: src/optee-src/ta/trusted_keys/user_ta_header_defines.h */
#define TA_UUID             TRUSTED_KEYS_UUID  /* 在 trusted_keys.h 中定义 */
#define TA_FLAGS            (TA_FLAG_SINGLE_INSTANCE | \
                             TA_FLAG_MULTI_SESSION | \
                             TA_FLAG_DEVICE_ENUM)
#define TA_STACK_SIZE       (4 * 1024)   /* TA 栈大小,字节 */
#define TA_DATA_SIZE        (16 * 1024)  /* TA 堆大小,字节 */
#define TA_VERSION          "1.0"
#define TA_DESCRIPTION      "Trusted Keys"
```

**关键属性位**(`TA_FLAGS`)的含义,完整定义在 [lib/libutee/include/user_ta_header.h](./src/optee-src/lib/libutee/include/user_ta_header.h) 中:

| 标志位 | 含义 | 选了会怎样 |
|--------|------|-----------|
| `TA_FLAG_SINGLE_INSTANCE` | 单实例:全局只一个 TA 实例 | 所有 CA 共享实例和 `sessionContext` 池 |
| `TA_FLAG_MULTI_SESSION` | 多会话:一个实例可被多次 OpenSession | 否则每次 OpenSession 都新建实例 |
| `TA_FLAG_INSTANCE_KEEP_ALIVE` | 最后一个 session 关闭后实例不销毁 | 避免重复初始化开销 |
| `TA_FLAG_DEVICE_ENUM` | 在 Linux TEE driver probe 时自动枚举 | 不依赖 tee-supplicant 即可被发现 |
| `TA_FLAG_DEVICE_ENUM_SUPP` | 等 tee-supplicant 启动后才枚举 | 依赖 supplicant 的 TA 用此位 |
| `TA_FLAG_DEVICE_ENUM_TEE_STORAGE_PRIVATE` | 等安全存储就绪后才枚举 | 用 RPMB/REE FS 存数据的 TA 用此位 |
| `TA_FLAG_SECURE_DATA_PATH` | 允许访问 SDP(Secure Data Path)内存 | 视频解码等场景 |
| `TA_FLAG_INSTANCE_KEEP_CRASHED` | TA 崩溃后不重启 | 调试用 |

**栈和堆的大小怎么定?** `TA_STACK_SIZE` 是 TA 调用栈大小(实际编译时还会加上 `TA_FRAMEWORK_STACK_SIZE = 2048` 字节用于 Trusted Core Framework)。`TA_DATA_SIZE` 是 TA 堆大小,`TEE_Malloc` 从这里分配。一个最小 TA 一般 4KB 栈 + 16KB 堆够用;加密大块数据时按需增大堆。

`ta_head` 结构体的实际布局在 [ta/user_ta_header.c](./src/optee-src/ta/user_ta_header.c) 中:

```c
/* 来源: src/optee-src/ta/user_ta_header.c (节选) */
#define TA_FRAMEWORK_STACK_SIZE 2048

const struct ta_head ta_head __section(".ta_head") = {
    .uuid = TA_UUID,
    .stack_size = TA_STACK_SIZE + TA_FRAMEWORK_STACK_SIZE,
    .flags = TA_FLAGS,
    .depr_entry = UINT64_MAX,    /* 旧入口字段,置为最大值表示"用新入口" */
};

uint8_t ta_heap[TA_DATA_SIZE];
const size_t ta_heap_size = sizeof(ta_heap);
```

`ta_head` 放在 ELF 的 `.ta_head` 段,OP-TEE 加载 TA 时读这个段拿到 UUID、栈/堆大小、属性位,据此分配 TA 地址空间。UUID 是 TA 的"门牌号",CA 通过 UUID 来 OpenSession。

### 1.3 一个最小的 hello TA

把入口点 + 命令分发组合起来,一个最小 TA 的核心代码(完整可编译版见第 5 节):

```c
/* hello_ta.c — 最小 TA 框架 */
#include <tee_internal_api.h>
#include <tee_internal_api_extensions.h>
#include <hello_ta.h>   /* 定义 UUID 和命令 ID */

TEE_Result TA_CreateEntryPoint(void)
{
    DMSG("TA 实例创建");
    return TEE_SUCCESS;
}

void TA_DestroyEntryPoint(void)
{
    DMSG("TA 实例销毁");
}

TEE_Result TA_OpenSessionEntryPoint(uint32_t pt __unused,
                                    TEE_Param params[TEE_NUM_PARAMS] __unused,
                                    void **sess_ctx __unused)
{
    DMSG("会话打开");
    return TEE_SUCCESS;
}

void TA_CloseSessionEntryPoint(void *sess_ctx __unused)
{
    DMSG("会话关闭");
}

TEE_Result TA_InvokeCommandEntryPoint(void *sess_ctx __unused,
                                      uint32_t cmd_id,
                                      uint32_t pt,
                                      TEE_Param params[TEE_NUM_PARAMS])
{
    switch (cmd_id) {
    case HELLO_TA_CMD_SAY:
        return cmd_say_hello(pt, params);
    default:
        EMSG("未知命令: 0x%x", cmd_id);
        return TEE_ERROR_NOT_SUPPORTED;
    }
}
```

**这就是 TA 的标准骨架**——5 个入口点 + 一个 switch-case 命令分发。后面所有 TA 都是这个模式,差别只在 `cmd_xxx` 的具体实现和它调用了哪些 Internal API。

> **核心要点**:TA 的骨架是"5 个入口点 + 命令分发"。`Create`/`Destroy` 管全局生命周期,`OpenSession`/`CloseSession` 管会话上下文,`InvokeCommand` 是真正业务入口。TA 的元数据(UUID/栈/堆/标志)在 `user_ta_header_defines.h` 中声明,由 `user_ta_header.c` 打包成 ELF 头。

---

## 2. GP Client API(CA 侧)

> 上一章讲了 TA 怎么写,但 CA 怎么调?本章介绍 GP Client API 的调用流程和参数类型,把 CA 侧的能力拉清楚。理解 CA 与 TA 的参数传递机制,是后续设计 TA 命令接口的基础。

### 2.1 Client API 调用流程

CA 调用 TA 的标准流程是 5 步,对应 5 个 GP Client API 函数:

```
1. TEEC_InitializeContext  → 初始化 CA 与 TEE 的连接上下文
2. TEEC_OpenSession        → 用 TA 的 UUID 打开会话
3. TEEC_InvokeCommand      → 触发 TA 的命令(可多次调用)
4. TEEC_CloseSession       → 关闭会话
5. TEEC_FinalizeContext    → 释放上下文
```

一个最小的 CA 调用骨架:

```c
/* hello_ca.c — 最小 CA 调用骨架 */
#include <tee_client_api.h>
#include <hello_ta.h>   /* 共享 UUID 和命令 ID 定义 */

int main(void)
{
    TEEC_Context ctx;
    TEEC_Session sess;
    TEEC_Operation op = {0};
    TEEC_Result res;
    uint32_t err_origin;

    /* 1. 初始化上下文,设备名 NULL 表示默认 /dev/tee0 */
    res = TEEC_InitializeContext(NULL, &ctx);
    if (res != TEEC_SUCCESS)
        return 1;

    /* 2. 用 TA 的 UUID 打开会话 */
    res = TEEC_OpenSession(&ctx, &sess, &HELLO_TA_UUID,
                           TEEC_LOGIN_PUBLIC, NULL, NULL, &err_origin);
    if (res != TEEC_SUCCESS)
        goto fin;

    /* 3. 调用命令 HELLO_TA_CMD_SAY,传一个输入字符串 */
    op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_INPUT,
                                     TEEC_NONE, TEEC_NONE, TEEC_NONE);
    op.params[0].tmpref.buffer = "world";
    op.params[0].tmpref.size = 5;

    res = TEEC_InvokeCommand(&sess, HELLO_TA_CMD_SAY,
                              &op, &err_origin);

    /* 4. 关闭会话 */
    TEEC_CloseSession(&sess);
fin:
    /* 5. 释放上下文 */
    TEEC_FinalizeContext(&ctx);
    return res;
}
```

**`TEEC_LOGIN_PUBLIC` 是什么?** GP 定义了几种客户端登录方式:`PUBLIC`(匿名)、`USER`(用户身份)、`GROUP`(组身份)、`APPLICATION`(应用身份)。`PUBLIC` 最简单,TA 拿到的客户端身份是匿名的——适合不要求鉴权的 TA。需要鉴权时(如只允许特定用户使用支付功能),用 `USER` 并在 TA 中检查 `TEE_GetPropertyAsIdentity` 返回的 uid。

### 2.2 参数类型:Value / TempMemory / RegisteredMemory

`TEEC_InvokeCommand` 通过 `TEEC_Operation` 传参。一个 operation 最多 4 个参数,每个参数的类型由 `paramTypes` 决定。GP 定义了三类参数:

| 参数类型 | 简写 | 数据来源 | 大小限制 | 典型用途 |
|---------|------|---------|----------|----------|
| **TEEC_Value** | VALUE | 两个 uint32_t (a, b) | 8 字节 | 传命令码、版本号、小数值 |
| **TEEC_TempMemory** | MEMREF_TEMP | CA 进程临时内存 | 无(受共享内存上限) | 一次性数据传输 |
| **TEEC RegisteredMemory** | MEMREF_* | 已注册的共享内存 | 无(共享内存上限) | 高频/大数据传输 |

**Value 参数**:最简单,直接在寄存器里传。适合传"长度"、"标志位"等小数值。

**TempMemory**:CA 临时申请的内存,libteec 自动把它注册到 TEE driver。调用结束后自动注销。**适合一次性调用**——但每次都要注册/注销,有开销。

**RegisteredMemory**:CA 先 `TEEC_RegisterSharedMemory` 注册一段内存,得到一个 SHM 句柄,然后多次 `InvokeCommand` 复用。**适合高频调用**——注册一次,反复用,省去每次注册开销。大数据(如视频流加密)首选。

每个参数还有方向:`INPUT`(只入)、`OUTPUT`(只出)、`INOUT`(双向)。组合起来用宏 `TEEC_PARAM_TYPES` 编码:

```c
/* 4 个参数的类型编码示例 */
op.paramTypes = TEEC_PARAM_TYPES(
    TEEC_MEMREF_TEMP_INPUT,    /* param[0]: 临时内存,只入 */
    TEEC_VALUE_OUTPUT,         /* param[1]: 值,只出 */
    TEEC_MEMREF_TEMP_INOUT,    /* param[2]: 临时内存,双向 */
    TEEC_NONE                  /* param[3]: 未用 */
);
```

**TA 侧对应**:TA 收到的 `TEE_Param params[4]` 与 CA 的 `op.params[4]` 一一对应,类型通过 `paramTypes`(在 TA 侧叫 `pt`)校验。TA 内部用 `TEE_PARAM_TYPES` 宏构造期望的类型,如果不匹配就返回 `TEE_ERROR_BAD_PARAMETERS`——这是 GP 推荐的"类型校验"模式:

```c
/* TA 侧的参数类型校验示例 */
static TEE_Result cmd_say_hello(uint32_t pt, TEE_Param params[TEE_NUM_PARAMS])
{
    const uint32_t exp_pt = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_INPUT,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE);
    if (pt != exp_pt)
        return TEE_ERROR_BAD_PARAMETERS;

    /* params[0].memref.buffer / .size 可安全使用 */
    /* ... */
    return TEE_SUCCESS;
}
```

> **核心要点**:Client API 的参数系统分三类——Value(小数值)、TempMemory(一次性数据)、RegisteredMemory(高频复用)。CA 与 TA 的参数通过 `TEEC_Operation` → 共享内存 → `TEE_Param` 传递,类型校验是 TA 的责任。

---

## 3. GP Internal API(TA 侧)

> 前两章讲了 TA 骨架和 CA 调用方式,但 TA 内部怎么"干活"?本章介绍 Internal API 的三大类能力:加密、安全存储、时间。这些 API 是 TA 区别于普通应用的关键——TA 能做的事,CA 做不到。

### 3.1 加密 API

GP Internal API 提供完整的密码学操作:对称加密、非对称加密、哈希、HMAC、AEAD、随机数。所有操作遵循"分配操作句柄 → 设置密钥 → 更新数据 → 完成"四步模式。

一个 AES-GCM 加密的完整流程:

```
1. TEE_AllocateOperation(&op, TEE_ALG_AES_GCM, TEE_MODE_ENCRYPT, key_len*8)
   → 分配操作句柄,指定算法和密钥长度(位)
2. TEE_AllocateTransientObject(&key, TEE_TYPE_AES, key_len*8)
   → 分配临时密钥对象
3. TEE_PopulateTransientObject(key, &attr, 1)
   → 用 attr(TEE_ATTR_SECRET_VALUE)填充密钥
4. TEE_SetOperationKey(op, key)
   → 把密钥绑定到操作
5. TEE_AEInit(op, iv, iv_len, tag_len*8, 0, 0)
   → 初始化 AEAD,设置 IV 和 tag 长度
6. TEE_AEEncryptFinal(op, plaintext, pt_len, ciphertext, &ct_len, tag, &tag_len)
   → 一次性完成加密(对小数据);大数据用 TEE_AEUpdate 分块
```

对应的源码实现入口在 [lib/libutee/tee_api_operations.c](./src/optee-src/lib/libutee/tee_api_operations.c)——所有 `TEE_*` 加密 API 都在这里转发为 syscall,进入 OP-TEE Core 的 `tee_svc_cryp.c`。下面是 `TEE_AllocateOperation` 的开头:

```c
/* 来源: src/optee-src/lib/libutee/tee_api_operations.c (节选) */
TEE_Result TEE_AllocateOperation(TEE_OperationHandle *operation,
                                 uint32_t algorithm, uint32_t mode,
                                 uint32_t maxKeySize)
{
    TEE_Result res;
    TEE_OperationHandle op = TEE_HANDLE_NULL;
    /* ... 算法检查、key size 校验 ... */
    op = TEE_Malloc(sizeof(struct __TEE_OperationHandle),
                    TEE_MALLOC_FILL_ZERO);
    if (!op)
        TEE_Panic(0);
    /* ... 后续设置 info 字段、调用 _utee_cryp_state_alloc ... */
    return res;
}
```

OP-TEE 支持的算法枚举(部分)在 `tee_api_defines.h` 中,包括 `TEE_ALG_AES_ECB`、`TEE_ALG_AES_CBC`、`TEE_ALG_AES_CTR`、`TEE_ALG_AES_GCM`、`TEE_ALG_SHA256`、`TEE_ALG_HMAC_SHA256`、`TEE_ALG_RSAES_PKCS1_V1_5`、`TEE_ALG_ECDSA_P256` 等。

**密钥来源**:TA 可以用两种密钥:

- **临时密钥**:`TEE_AllocateTransientObject` + `TEE_PopulateTransientObject`,密钥数据在 TA 内存中,操作完成后 `TEE_FreeTransientObject` 释放。适合 CA 传入的会话密钥。
- **持久化密钥**:`TEE_CreatePersistentObject` 把密钥存到安全存储,后续用 `TEE_OpenPersistentObject` 取回。适合长期密钥(如设备密钥)。

**硬件唯一密钥(HUK)**:某些 TA 需要一个"绑定到设备"的密钥——例如把数据加密后存到 REE FS,只有同一台设备能解密。这通过 PTA (Pseudo TA) `PTA_SYSTEM_DERIVE_TA_UNIQUE_KEY` 提供,调用方式见第 5 节示例。底层是 OP-TEE 的 `huk_subkey` 机制,从芯片 OTP (One-Time Programmable) 中的 HUK 派生出"TA UUID 绑定"的子密钥——不同 TA 拿到不同子密钥,且只有同一台设备能算出同一子密钥。

### 3.2 安全存储 API

GP 安全存储 API 把"数据持久化到 TEE 信任的存储"标准化。核心是"持久化对象"概念:每个对象有 ID(名字)、数据流、属性表。

```c
/* 创建一个持久化对象,初始数据 "secret" */
TEE_ObjectHandle h;
uint32_t flags = TEE_DATA_FLAG_ACCESS_READ |
                 TEE_DATA_FLAG_ACCESS_WRITE |
                 TEE_DATA_FLAG_OVERWRITE;
const char *data = "secret";
TEE_Result res = TEE_CreatePersistentObject(
    TEE_STORAGE_PRIVATE,        /* 存储后端 */
    "my_secret",                /* object ID */
    strlen("my_secret"),
    flags,
    TEE_HANDLE_NULL,            /* attributes(无) */
    data, strlen(data),
    &h);
```

之后读取:

```c
TEE_ObjectHandle h;
TEE_Result res = TEE_OpenPersistentObject(
    TEE_STORAGE_PRIVATE,
    "my_secret", strlen("my_secret"),
    TEE_DATA_FLAG_ACCESS_READ,
    &h);

char buf[32];
size_t count;
TEE_ReadObjectData(h, buf, sizeof(buf), &count);
TEE_CloseObject(h);
```

**存储后端**:`TEE_STORAGE_PRIVATE` 是个抽象,实际后端由 OP-TEE 编译配置决定:

- `CFG_RPMB_FS=y` → 用 RPMB(抗回滚)
- `CFG_REE_FS=y` → 用 REE FS(加密 + 哈希树)

这两种后端的细节见第 4 节。

源码入口在 [lib/libutee/tee_api_objects.c](./src/optee-src/lib/libutee/tee_api_objects.c):

```c
/* 来源: src/optee-src/lib/libutee/tee_api_objects.c (节选) */
TEE_Result TEE_OpenPersistentObject(uint32_t storageID, const void *objectID,
                                    size_t objectIDLen, uint32_t flags,
                                    TEE_ObjectHandle *object)
{
    TEE_Result res;
    uint32_t obj;
    __utee_check_out_annotation(object, sizeof(*object));
    res = _utee_storage_obj_open(storageID, objectID, objectIDLen, flags, &obj);
    if (res == TEE_SUCCESS)
        *object = (TEE_ObjectHandle)(uintptr_t)obj;
    /* ... 错误处理 ... */
    return res;
}
```

`_utee_storage_obj_open` 是 syscall,进入 OP-TEE Core 的 `tee_svc_storage.c`,后者根据 `storageID` 分发到 `tee_rpmb_fs.c` 或 `tee_ree_fs.c`。

### 3.3 时间 API

TA 经常需要时间:生成证书的有效期、限时令牌、性能测量。GP 提供三套时间 API:

| API | 来源 | 单调性 | 用途 |
|-----|------|--------|------|
| `TEE_GetSystemTime` | 系统 timecounter | 单调递增 | 性能测量、超时判断 |
| `TEE_GetTAPersistentTime` | 持久化时间(经安全存储维护) | 跨 TA 重启延续 | 证书有效期 |
| `TEE_GetREETime` | REE 系统时钟 | 可被 REE 修改 | 显示给用户的时钟(不可信) |

**为什么有三种?** 因为它们对"REE 攻击者篡改时间"的抵抗力不同。`TEE_GetREETime` 直接读 REE 时钟,REE 改了就跟着变——不可信。`TEE_GetSystemTime` 读硬件定时器(如 ARM 的 `CNTVCT_EL0`),单调递增,REE 无法回拨——可信。`TEE_GetTAPersistentTime` 用安全存储维护一个"上次 TA 退出时的时间 + 系统时间增量",即使 REE 改了时钟,TA 重启后也能根据系统时间增量恢复——抗 REE 时钟攻击。

源码入口在 [lib/libutee/tee_api.c](./src/optee-src/lib/libutee/tee_api.c),通过 `_utee_get_time` syscall 进入 OP-TEE Core 的 `tee_time*.c`。

> **核心要点**:Internal API 提供三大能力:加密(操作句柄 + 密钥对象 + Update/Final 模式)、安全存储(持久化对象 + RPMB/REE FS 后端)、时间(系统/持久化/REE 三种,信任级别递减)。CA 无法直接调用这些 API,只能通过 TA 间接使用——这是 TEE 的"特权 API 边界"。

---

## 4. 安全存储后端

> 上一章的存储 API 只说了"怎么用",没说"数据到底存在哪、怎么保证安全"。本章把两个后端(RPMB 和 REE FS)讲清楚,并解释 tee-supplicant 在其中的角色。理解存储后端,才能正确选择 `TA_FLAG_DEVICE_ENUM_*` 标志和设计 TA 的初始化时机。

### 4.1 RPMB:抗回滚的硬件支持

RPMB (Replay Protected Memory Block) 是 eMMC/UFS 标准定义的一个特殊分区,有以下特性:

- **写前认证**:每次写都要带一个 HMAC-SHA256 签名,密钥在出厂时烧入 RPMB(通常 32 字节)
- **写计数器**:RPMB 内部维护一个单调递增的写计数器,每次写自动 +1,无法回拨
- **抗回滚**:即使攻击者把存储芯片换成旧版本,写计数器不匹配,数据被拒绝

**为什么 OP-TEE 要用 RPMB?** 安全存储最大的威胁是"回滚攻击"——攻击者把整个文件系统恢复到旧版本,让 TA 以为"密钥还没轮换过",从而用旧密钥解密已被攻击者获取的密文。RPMB 的写计数器从硬件层面杜绝了这种攻击:每次写 OP-TEE 的安全存储,计数器 +1,OP-TEE 内部记录"最大看到的计数器值",重启后如果发现当前计数器 < 记录值,说明被回滚,拒绝使用。

源码在 [core/tee/tee_rpmb_fs.c](./src/optee-src/core/tee/tee_rpmb_fs.c),关键定义:

```c
/* 来源: src/optee-src/core/tee/tee_rpmb_fs.c (节选) */
#define RPMB_STORAGE_START_ADDRESS      0
#define RPMB_FS_FAT_START_ADDRESS       512
#define RPMB_BLOCK_SIZE_SHIFT           8      /* 256 字节/块 */
#define RPMB_FS_MAGIC                   0x52504D42  /* "RPMB" */
#define TEE_RPMB_FS_FILENAME_LENGTH     224
```

RPMB 上 OP-TEE 自己维护一个简化的 FAT 文件系统(`RPMB_FS_MAGIC` 标识),每个文件有固定长度的文件名和元数据。

**RPMB 的依赖**:RPMB 的 HMAC 密钥通常在出厂时通过安全通道烧入。开发板上没有这个密钥,RPMB 写入会失败——这时 OP-TEE 退回到 REE FS 后端。

### 4.2 REE FS:加密 + 哈希树

`CFG_REE_FS=y` 时,OP-TEE 把安全存储数据放在 REE 的文件系统中(典型路径 `/data/tee/`),但用两层保护:

1. **加密**:每个文件用 FEK (File Encryption Key) 加密,FEK 又用 TA 的派生密钥加密后存在文件元数据中
2. **哈希树**:文件元数据组织成 Merkle 哈希树,任何篡改都会导致哈希不匹配,被检测到

安全存储系统的架构分三层:
- **上层**:GP TEE Storage API(TA 调用)
- **中间**:OP-TEE Core 的存储管理(加密、哈希树)
- **底层**:两个后端——RPMB(抗回滚)和 REE FS(大容量)

TA 通过统一的 API 访问存储,底层后端对 TA 透明。

源码在 [core/tee/tee_ree_fs.c](./src/optee-src/core/tee/tee_ree_fs.c) 和 [core/tee/fs_htree.c](./src/optee-src/core/tee/fs_htree.c)。

**REE FS 的局限**:无法抗回滚——攻击者可以把 `/data/tee/` 整个目录回滚到旧版本,哈希树依然匹配(因为旧版本也是合法加密的)。所以 REE FS 适合"防篡改"但不要求"防回滚"的场景。

**为什么还要 REE FS?** 因为 RPMB 容量小(典型 4MB)、写入有寿命限制(eMMC RPMB 通常 1 万次写)。REE FS 容量大、写入快。两者结合:REE FS 存数据,RPMB 存"防回滚计数器"——这是 OP-TEE 的默认配置(`CFG_REE_FS=y` + `CFG_RPMB_FS=y`)。

**元数据加密结构**:每个安全存储文件包含两部分——元数据(encrypted metadata)和数据块(encrypted block data)。元数据包含 FEK(文件加密密钥)、文件大小、时间戳等,用 TA 的派生密钥加密。数据块用 FEK 加密。元数据组织成哈希树(Merkle tree),根哈希存储在安全内存中,防止篡改。

**数据块加密方式**:每个数据块(data block)用 FEK + 块索引(block index)作为 IV 进行 AES-CTR 加密。这种设计确保相同明文在不同块中加密结果不同,防止重放攻击。加密后的数据块存储在 REE 文件系统中,即使 REE 被攻破,攻击者也无法解密(因为 FEK 存储在安全内存中)。

### 4.3 tee-supplicant 的角色

OP-TEE Core 自己**不能**直接访问 eMMC 或 REE 文件系统——这些驱动在 Linux 内核里。所以 OP-TEE 通过 RPC 反向请求 tee-supplicant 代为执行:

| 操作 | OP-TEE Core 发起的 RPC | tee-supplicant 实际做的事 |
|------|----------------------|------------------------|
| RPMB 读写 | `OPTEE_RPC_CMD_RPC_CMD RPMB` | 调用 eMMC ioctl 读写 RPMB 分区 |
| REE FS 读写 | `OPTEE_RPC_CMD_FS` | 读写 `/data/tee/<file>` |
| TA 加载 | `OPTEE_RPC_CMD_LOAD_TA` | 从 `/lib/optee_armtz/` 读 TA ELF |
| 插件调用 | `OPTEE_RPC_CMD_SUPPL_PLUGIN` | 调用注册的 REE 侧插件 |
| 定时器分配 | `OPTEE_RPC_CMD_TIMER` | 创建 REE 定时器(已少用,内核支持异步通知后) |

**TA 何时需要 supplicant?** 取决于 `TA_FLAG_DEVICE_ENUM_*`:

- `TA_FLAG_DEVICE_ENUM`:不需要 supplicant,Linux TEE driver probe 时自动枚举(因为不依赖存储)
- `TA_FLAG_DEVICE_ENUM_SUPP`:需要 supplicant 启动后才枚举
- `TA_FLAG_DEVICE_ENUM_TEE_STORAGE_PRIVATE`:需要安全存储就绪后才枚举(若用 RPMB 则等内核 RPMB 路由,若用 REE FS 则等 supplicant)

**实例**:trusted_keys TA 用 `TA_FLAG_DEVICE_ENUM`(见 1.2 节),因为它只做加密运算,不依赖存储。avb TA(用于 Android Verified Boot)用 `TA_FLAG_SINGLE_INSTANCE | TA_FLAG_MULTI_SESSION`,但实际依赖 RPMB 存储回滚计数器,所以加载时机由平台配置决定。

> **核心要点**:OP-TEE 安全存储有两个后端——RPMB(抗回滚,小容量,需 supplicant 代理)和 REE FS(防篡改,大容量,不抗回滚)。两者常组合使用。tee-supplicant 是 REE 侧守护进程,代 OP-TEE 完成 RPMB/REE FS/TA 加载等操作——这是 OP-TEE 保持精简的关键设计。

---

## 5. 完整例子:密钥保管 TA

> 前四章讲了 TA 骨架、CA 调用、Internal API、存储后端,但都是片段。本章用一个完整的"密钥保管 TA"把所有知识点串起来:CA 传入明文密钥 → TA 用 HUK 派生密钥加密后存到安全存储 → CA 取回时 TA 解密返回。完整代码 + 编译说明,可直接上手。

### 5.1 需求与设计

**场景**:一个 Android 应用需要保管一个用户密码(用于本地加密用户数据)。要求:

1. 密码不能以明文出现在 REE 内存
2. 加密后的密码可以持久化,TA 重启后仍可取回
3. 只有同一台设备能解密(防止镜像克隆)

**设计**:

- CA 通过 `TEEC_InvokeCommand` 调用两个命令:`CMD_STORE`(存)和 `CMD_LOAD`(取)
- TA 收到明文后,用 HUK 派生的设备绑定密钥 AES-GCM 加密,存到 `TEE_STORAGE_PRIVATE`
- 取回时反向:从存储读出密文,用同一密钥解密,返回明文给 CA

参考实现:OP-TEE 官方的 [ta/trusted_keys/](./src/optee-src/ta/trusted_keys/) 做的正是类似的事(seal/unseal 密钥)。我们的例子基于它简化而来。

### 5.2 TA 头文件:keystore_ta.h

```c
/* keystore_ta.h — TA 与 CA 共享的头文件 */
#ifndef KEYSTORE_TA_H
#define KEYSTORE_TA_H

/* TA 的 UUID(用 uuidgen 生成) */
#define KEYSTORE_TA_UUID \
    { 0x1b484ea5, 0xa6c4, 0x4b1c, \
      { 0x9a, 0x2e, 0x7c, 0x44, 0x5f, 0x66, 0x11, 0xaa } }

/* 命令 ID */
#define KEYSTORE_CMD_STORE   0   /* 存密钥:[in] memref[0]=明文 */
#define KEYSTORE_CMD_LOAD    1   /* 取密钥:[out] memref[0]=明文 */

#endif /* KEYSTORE_TA_H */
```

### 5.3 TA 属性:user_ta_header_defines.h

```c
/* user_ta_header_defines.h */
#ifndef USER_TA_HEADER_DEFINES_H
#define USER_TA_HEADER_DEFINES_H

#include <keystore_ta.h>

#define TA_UUID             KEYSTORE_TA_UUID

/* 单实例 + 多会话 + 等安全存储就绪枚举 */
#define TA_FLAGS            (TA_FLAG_SINGLE_INSTANCE | \
                             TA_FLAG_MULTI_SESSION | \
                             TA_FLAG_DEVICE_ENUM_TEE_STORAGE_PRIVATE)

#define TA_STACK_SIZE       (4 * 1024)
#define TA_DATA_SIZE        (16 * 1024)

#define TA_VERSION          "1.0"
#define TA_DESCRIPTION      "Key Store TA"

#endif
```

注意 `TA_FLAG_DEVICE_ENUM_TEE_STORAGE_PRIVATE`——这个 TA 依赖安全存储,所以等存储就绪后再枚举。

### 5.4 TA 实现:keystore_ta.c

```c
/* keystore_ta.c — 完整 TA 实现 */
#include <tee_internal_api.h>
#include <tee_internal_api_extensions.h>
#include <pta_system.h>           /* PTA_SYSTEM_DERIVE_TA_UNIQUE_KEY */
#include <keystore_ta.h>
#include <string.h>

#define OBJ_ID         "keystore.blob"
#define OBJ_ID_LEN     (sizeof(OBJ_ID) - 1)
#define IV_SIZE        12
#define TAG_SIZE       16
#define KEY_SIZE       32          /* AES-256 派生密钥 */

/*
 * 密文布局:IV(12) || TAG(16) || 加密数据
 */
struct blob_header {
    uint8_t iv[IV_SIZE];
    uint8_t tag[TAG_SIZE];
};

/* 从 HUK 派生设备绑定密钥 */
static TEE_Result derive_key(uint8_t *key, size_t key_len)
{
    TEE_TASessionHandle sess = TEE_HANDLE_NULL;
    TEE_Param params[TEE_NUM_PARAMS] = {0};
    TEE_Result res;
    uint32_t ret_orig;
    uint32_t pt = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_INPUT,
                                  TEE_PARAM_TYPE_MEMREF_OUTPUT,
                                  TEE_PARAM_TYPE_NONE,
                                  TEE_PARAM_TYPE_NONE);
    /* 打开 system PTA(伪 TA),请求派生 TA 唯一密钥 */
    res = TEE_OpenTASession(&(const TEE_UUID)PTA_SYSTEM_UUID,
                            TEE_TIMEOUT_INFINITE, 0, NULL,
                            &sess, &ret_orig);
    if (res != TEE_SUCCESS)
        return res;

    params[1].memref.buffer = key;
    params[1].memref.size = key_len;

    res = TEE_InvokeTACommand(sess, TEE_TIMEOUT_INFINITE,
                              PTA_SYSTEM_DERIVE_TA_UNIQUE_KEY,
                              pt, params, &ret_orig);
    TEE_CloseTASession(sess);
    return res;
}

/* AES-GCM 加密:把 in 加密成 out(含 IV/TAG 头) */
static TEE_Result aes_gcm_encrypt(const uint8_t *key, size_t key_len,
                                  const uint8_t *in, size_t in_len,
                                  uint8_t *out, size_t *out_len)
{
    TEE_OperationHandle op = TEE_HANDLE_NULL;
    TEE_ObjectHandle hkey = TEE_HANDLE_NULL;
    TEE_Attribute attr = {0};
    struct blob_header *hdr = (struct blob_header *)out;
    size_t enc_len = in_len;
    size_t tag_len = TAG_SIZE;
    TEE_Result res;

    res = TEE_AllocateOperation(&op, TEE_ALG_AES_GCM,
                                TEE_MODE_ENCRYPT, key_len * 8);
    if (res != TEE_SUCCESS)
        return res;

    res = TEE_AllocateTransientObject(TEE_TYPE_AES, key_len * 8, &hkey);
    if (res != TEE_SUCCESS)
        goto out_op;

    attr.attributeID = TEE_ATTR_SECRET_VALUE;
    attr.content.ref.buffer = (void *)key;
    attr.content.ref.length = key_len;

    res = TEE_PopulateTransientObject(hkey, &attr, 1);
    if (res != TEE_SUCCESS)
        goto out_key;

    res = TEE_SetOperationKey(op, hkey);
    if (res != TEE_SUCCESS)
        goto out_key;

    TEE_GenerateRandom(hdr->iv, IV_SIZE);   /* 随机 IV */

    res = TEE_AEInit(op, hdr->iv, IV_SIZE, TAG_SIZE * 8, 0, 0);
    if (res != TEE_SUCCESS)
        goto out_key;

    res = TEE_AEEncryptFinal(op, in, in_len,
                             out + sizeof(*hdr), &enc_len,
                             hdr->tag, &tag_len);
    if (res != TEE_SUCCESS || tag_len != TAG_SIZE)
        goto out_key;

    *out_len = sizeof(*hdr) + enc_len;

out_key:
    TEE_FreeTransientObject(hkey);
out_op:
    TEE_FreeOperation(op);
    return res;
}

/* AES-GCM 解密:逆操作 */
static TEE_Result aes_gcm_decrypt(const uint8_t *key, size_t key_len,
                                  const uint8_t *in, size_t in_len,
                                  uint8_t *out, size_t *out_len)
{
    TEE_OperationHandle op = TEE_HANDLE_NULL;
    TEE_ObjectHandle hkey = TEE_HANDLE_NULL;
    TEE_Attribute attr = {0};
    const struct blob_header *hdr = (const struct blob_header *)in;
    size_t enc_len = in_len - sizeof(*hdr);
    TEE_Result res;

    if (in_len <= sizeof(*hdr))
        return TEE_ERROR_BAD_PARAMETERS;

    res = TEE_AllocateOperation(&op, TEE_ALG_AES_GCM,
                                TEE_MODE_DECRYPT, key_len * 8);
    if (res != TEE_SUCCESS)
        return res;

    res = TEE_AllocateTransientObject(TEE_TYPE_AES, key_len * 8, &hkey);
    if (res != TEE_SUCCESS)
        goto out_op;

    attr.attributeID = TEE_ATTR_SECRET_VALUE;
    attr.content.ref.buffer = (void *)key;
    attr.content.ref.length = key_len;

    res = TEE_PopulateTransientObject(hkey, &attr, 1);
    if (res != TEE_SUCCESS)
        goto out_key;

    res = TEE_SetOperationKey(op, hkey);
    if (res != TEE_SUCCESS)
        goto out_key;

    res = TEE_AEInit(op, hdr->iv, IV_SIZE, TAG_SIZE * 8, 0, 0);
    if (res != TEE_SUCCESS)
        goto out_key;

    res = TEE_AEDecryptFinal(op, in + sizeof(*hdr), enc_len,
                             out, out_len, hdr->tag, TAG_SIZE);

out_key:
    TEE_FreeTransientObject(hkey);
out_op:
    TEE_FreeOperation(op);
    return res;
}

/* CMD_STORE:加密 + 持久化 */
static TEE_Result cmd_store(uint32_t pt, TEE_Param params[TEE_NUM_PARAMS])
{
    const uint32_t exp_pt = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_INPUT,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE);
    uint8_t key[KEY_SIZE];
    uint8_t *buf;
    size_t buf_len;
    TEE_ObjectHandle h = TEE_HANDLE_NULL;
    uint32_t flags = TEE_DATA_FLAG_ACCESS_READ |
                     TEE_DATA_FLAG_ACCESS_WRITE |
                     TEE_DATA_FLAG_OVERWRITE;
    TEE_Result res;

    if (pt != exp_pt)
        return TEE_ERROR_BAD_PARAMETERS;

    res = derive_key(key, sizeof(key));
    if (res != TEE_SUCCESS)
        return res;

    buf_len = sizeof(struct blob_header) + params[0].memref.size;
    buf = TEE_Malloc(buf_len, TEE_MALLOC_FILL_ZERO);
    if (!buf) {
        res = TEE_ERROR_OUT_OF_MEMORY;
        goto out;
    }

    res = aes_gcm_encrypt(key, sizeof(key),
                          params[0].memref.buffer, params[0].memref.size,
                          buf, &buf_len);
    if (res != TEE_SUCCESS)
        goto out;

    res = TEE_CreatePersistentObject(TEE_STORAGE_PRIVATE,
                                     OBJ_ID, OBJ_ID_LEN,
                                     flags, TEE_HANDLE_NULL,
                                     buf, buf_len, &h);
    if (h != TEE_HANDLE_NULL)
        TEE_CloseObject(h);
out:
    TEE_Free(buf);
    memzero_explicit(key, sizeof(key));
    return res;
}

/* CMD_LOAD:读出 + 解密 */
static TEE_Result cmd_load(uint32_t pt, TEE_Param params[TEE_NUM_PARAMS])
{
    const uint32_t exp_pt = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_OUTPUT,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE,
                                            TEE_PARAM_TYPE_NONE);
    uint8_t key[KEY_SIZE];
    uint8_t *buf;
    size_t buf_len;
    TEE_ObjectHandle h = TEE_HANDLE_NULL;
    size_t count;
    TEE_Result res;

    if (pt != exp_pt)
        return TEE_ERROR_BAD_PARAMETERS;

    res = TEE_OpenPersistentObject(TEE_STORAGE_PRIVATE,
                                   OBJ_ID, OBJ_ID_LEN,
                                   TEE_DATA_FLAG_ACCESS_READ, &h);
    if (res != TEE_SUCCESS)
        return res;

    /* 先查大小 */
    res = TEE_SeekObjectData(h, 0, TEE_DATA_SEEK_END);
    if (res != TEE_SUCCESS)
        goto out;
    res = TEE_SeekObjectData(h, 0, TEE_DATA_SEEK_SET);
    if (res != TEE_SUCCESS)
        goto out;

    /* 简化:用 TA_DATA_SIZE 作为读缓冲上限 */
    buf_len = TA_DATA_SIZE;
    buf = TEE_Malloc(buf_len, TEE_MALLOC_FILL_ZERO);
    if (!buf) {
        res = TEE_ERROR_OUT_OF_MEMORY;
        goto out;
    }

    res = TEE_ReadObjectData(h, buf, buf_len, &count);
    TEE_CloseObject(h);
    h = TEE_HANDLE_NULL;
    if (res != TEE_SUCCESS)
        goto out;

    res = derive_key(key, sizeof(key));
    if (res != TEE_SUCCESS)
        goto out;

    buf_len = params[0].memref.size;   /* 输出缓冲大小 */
    res = aes_gcm_decrypt(key, sizeof(key),
                          buf, count,
                          params[0].memref.buffer, &buf_len);
    if (res == TEE_SUCCESS)
        params[0].memref.size = buf_len;
out:
    TEE_Free(buf);
    if (h != TEE_HANDLE_NULL)
        TEE_CloseObject(h);
    memzero_explicit(key, sizeof(key));
    return res;
}

/* —— 5 个入口点 —— */

TEE_Result TA_CreateEntryPoint(void) { return TEE_SUCCESS; }
void TA_DestroyEntryPoint(void)      { }

TEE_Result TA_OpenSessionEntryPoint(uint32_t pt __unused,
                                    TEE_Param p[TEE_NUM_PARAMS] __unused,
                                    void **ctx __unused)
{
    return TEE_SUCCESS;
}

void TA_CloseSessionEntryPoint(void *ctx __unused) { }

TEE_Result TA_InvokeCommandEntryPoint(void *ctx __unused,
                                      uint32_t cmd, uint32_t pt,
                                      TEE_Param params[TEE_NUM_PARAMS])
{
    switch (cmd) {
    case KEYSTORE_CMD_STORE: return cmd_store(pt, params);
    case KEYSTORE_CMD_LOAD:  return cmd_load(pt, params);
    default:                 return TEE_ERROR_NOT_SUPPORTED;
    }
}
```

**几个关键设计点**:

1. **HUK 派生**:`derive_key()` 调用 `PTA_SYSTEM_DERIVE_TA_UNIQUE_KEY`,从硬件 HUK 派生出绑定到本 TA UUID 的子密钥。同一台设备、同一 TA 始终拿到同一密钥;不同 TA 拿到不同密钥;不同设备拿到不同密钥。
2. **IV 随机化**:每次加密都 `TEE_GenerateRandom` 生成新 IV,即使明文相同,密文也不同——防模式分析。
3. **AES-GCM 而非 CBC**:GCM 同时提供保密性和完整性,任何篡改都会被 `TEE_AEDecryptFinal` 检测出。
4. **密钥清零**:`memzero_explicit(key, sizeof(key))` 确保派生密钥用完后从内存中彻底擦除,不被后续 heap 复用泄露。

参考实现可对比 [ta/trusted_keys/entry.c](./src/optee-src/ta/trusted_keys/entry.c)——它做的是 seal/unseal(用 HUK 加密任意密钥),本文例子是 seal/unseal 任意数据,本质相同。

### 5.5 CA 实现:keystore_ca.c

```c
/* keystore_ca.c — 客户端应用 */
#include <tee_client_api.h>
#include <keystore_ta.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char *argv[])
{
    TEEC_Context ctx;
    TEEC_Session sess;
    TEEC_Operation op = {0};
    TEEC_Result res;
    uint32_t err_origin;
    char buf[256];

    if (argc != 2 || (strcmp(argv[1], "store") && strcmp(argv[1], "load"))) {
        fprintf(stderr, "用法: %s <store|load>\n", argv[0]);
        return 1;
    }

    res = TEEC_InitializeContext(NULL, &ctx);
    if (res != TEEC_SUCCESS) {
        fprintf(stderr, "InitContext 失败: 0x%x\n", res);
        return 1;
    }

    res = TEEC_OpenSession(&ctx, &sess, &KEYSTORE_TA_UUID,
                           TEEC_LOGIN_PUBLIC, NULL, NULL, &err_origin);
    if (res != TEEC_SUCCESS) {
        fprintf(stderr, "OpenSession 失败: 0x%x\n", res);
        goto fin;
    }

    if (!strcmp(argv[1], "store")) {
        /* 从 stdin 读明文(实际场景应由 CA 内部生成,不应跨 REE 传) */
        size_t n = fread(buf, 1, sizeof(buf), stdin);
        op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_INPUT,
                                         TEEC_NONE, TEEC_NONE, TEEC_NONE);
        op.params[0].tmpref.buffer = buf;
        op.params[0].tmpref.size = n;
        res = TEEC_InvokeCommand(&sess, KEYSTORE_CMD_STORE, &op, &err_origin);
        printf("store 结果: 0x%x\n", res);
    } else {
        op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_OUTPUT,
                                         TEEC_NONE, TEEC_NONE, TEEC_NONE);
        op.params[0].tmpref.buffer = buf;
        op.params[0].tmpref.size = sizeof(buf);
        res = TEEC_InvokeCommand(&sess, KEYSTORE_CMD_LOAD, &op, &err_origin);
        if (res == TEEC_SUCCESS) {
            fwrite(buf, 1, op.params[0].tmpref.size, stdout);
            putchar('\n');
        } else {
            fprintf(stderr, "load 失败: 0x%x\n", res);
        }
    }

    TEEC_CloseSession(&sess);
fin:
    TEEC_FinalizeContext(&ctx);
    return res;
}
```

### 5.6 编译与部署

**编译 TA**:需要 `TA_DEV_KIT_DIR`——OP-TEE 编译时生成的 TA 开发包(含头文件、libutee、链接脚本)。

```bash
# 假设 OP-TEE 源码在 /opt/optee
export TA_DEV_KIT_DIR=/opt/optee/out/arm-plat-vexpress/export-ta_arm64
export CROSS_COMPILE=aarch64-linux-gnu-

# 在 TA 目录下(含 keystore_ta.c / keystore_ta.h /
#             user_ta_header_defines.h / Makefile)
make
# 产物:  <UUID>.ta  (ELF,strip 后约 10-30KB)
```

TA 的 Makefile 极简,参考 [ta/avb/Makefile](./src/optee-src/ta/avb/Makefile):

```makefile
BINARY=1b484ea5-a6c4-4b1c-9a2e-7c445f6611aa
CROSS_COMPILE ?= aarch64-linux-gnu-
-include $(TA_DEV_KIT_DIR)/mk/ta_dev_kit.mk
```

`BINARY` 就是 TA UUID,Makefile 会用 `ta_dev_kit.mk` 提供的规则编译链接出 `<UUID>.ta`。

**编译 CA**:链接 libteec(optee_client 提供):

```bash
aarch64-linux-gnu-gcc keystore_ca.c -o keystore_ca \
    -I/opt/optee_client/out/include \
    -L/opt/optee_client/out/lib -lteec
```

**部署到目标板**:

```bash
# TA 部署到 /lib/optee_armtz/(tee-supplicant 默认从此目录加载 TA)
scp 1b484ea5-a6c4-4b1c-9a2e-7c445f6611aa.ta root@target:/lib/optee_armtz/

# CA 部署到任意位置
scp keystore_ca root@target:/usr/local/bin/

# 确保 tee-supplicant 运行
ssh root@target systemctl status tee-supplicant
```

**运行**:

```bash
# 存
echo -n "my-super-secret" | keystore_ca store

# 取
keystore_ca load
# 输出: my-super-secret
```

> **核心要点**:TA 开发的完整流程是"5 个入口点 + 命令分发 + Internal API"。本例用 HUK 派生 + AES-GCM + 持久化对象实现了"设备绑定的密钥保管"——这是 OP-TEE 上最常见的安全业务模式。TA 通过 `TA_DEV_KIT_DIR` 编译,部署到 `/lib/optee_armtz/`,CA 链接 libteec 即可调用。

---

## 6. 远程证明简介

> 前五章讲了 TA 怎么开发和存储数据,但还有一个问题:CA(或远端服务器)怎么知道"对面的 TA 真的是这个 TA、且运行在未被篡改的 TEE 上"?这就是远程证明要解决的问题。本章做概念性介绍,深入实现超出本文范围。

远程证明 (Remote Attestation) 的核心思路:**TEE 用一个设备私钥对"TA 度量值"签名,远端用对应的公钥(或证书链)验证**。

OP-TEE 中的相关机制:

1. **TA 度量**:TA 加载时,OP-TEE 计算 TA ELF 的 SHA-256,作为度量值
2. **设备密钥**:从 HUK 派生出 attestation key,只在 TEE 内可用
3. **证明 PTA**:OP-TEE 提供 [pta_attestation.h](./src/optee-src/lib/libutee/include/pta_attestation.h) 定义的伪 TA,TA 可调用它获取自己的度量值或签名
4. **验证方**:远端服务器用厂商 CA (Certificate Authority) 颁发的证书链验证签名,确认设备真实性和 TA 完整性

远程证明的典型流程:

```
1. CA → TA: 发起一个挑战(随机数 nonce)
2. TA → PTA_SYSTEM: 请求获取本 TA 的度量值和签名
3. PTA 用设备 attestation key 对 (nonce || 度量值) 签名
4. TA → CA: 返回签名 + 度量值 + 设备证书
5. CA → 服务器: 转发以上数据
6. 服务器: 用证书链验证签名,对比度量值白名单
7. 服务器 → CA: 验证通过 / 失败
```

**为什么需要 nonce?** 防止重放攻击。如果攻击者录下"一次成功的证明响应",下次重放就能伪装成合法 TA。nonce 是一次性随机数,TA 把它放进签名内容,重放时 nonce 不匹配,验证失败。

OP-TEE 的远程证明实现还在演进中,最新的版本支持与 Veraison 等标准证明框架对接([pta_veraison_attestation.h](./src/optee-src/lib/libutee/include/pta_veraison_attestation.h))。

---

## 7. 总结

把全文要点收一下:

- **TA 骨架**:5 个入口点(`Create`/`Destroy`/`OpenSession`/`CloseSession`/`InvokeCommand`)+ 命令分发 switch-case + 属性头(UUID/栈/堆/标志)。这是所有 GP TA 的标准模式。
- **CA 调用**:5 步流程(InitContext → OpenSession → InvokeCommand → CloseSession → FinalizeContext),参数类型分 Value/TempMemory/RegisteredMemory。
- **Internal API**:加密(操作句柄 + Update/Final)、安全存储(持久化对象 + RPMB/REE FS 后端)、时间(系统/持久化/REE 三种)。
- **存储后端**:RPMB 抗回滚但容量小,REE FS 防篡改但不抗回滚,常组合使用。tee-supplicant 代 TEE 完成存储操作。
- **完整例子**:密钥保管 TA 用 HUK 派生 + AES-GCM + 持久化对象实现设备绑定的密钥保管。
- **远程证明**:TEE 用设备 attestation key 对 TA 度量值签名,远端验证——解决"对面的 TA 是否可信"问题。

**写 TA 的核心模式**:"入口点 + 命令分发 + 安全 API"。CA 把请求通过 SMC 送进来,TA 在 `TA_InvokeCommandEntryPoint` 里分发,真正干活的是 Internal API。掌握这个模式后,任何 GP 兼容 TEE(不只 OP-TEE)上的 TA 开发都大同小异——这就是 GP 标准的价值。

下一篇 [09-opensbi-riscv-counterpart.md](./09-opensbi-riscv-counterpart.md) 转到 RISC-V 阵营,看 OpenSBI 怎么对应 TF-A 的角色。

---

## 参考资料

- [GlobalPlatform TEE Internal Core API Specification v1.3.1](https://globalplatform.org/specs-library/) — TA 侧 API 标准
- [GlobalPlatform TEE Client API Specification v1.0](https://globalplatform.org/specs-library/) — CA 侧 API 标准
- [OP-TEE Documentation — Trusted Applications](https://optee.readthedocs.io/en/latest/build/devices.html#trusted-applications) — TA 开发文档
- [OP-TEE Documentation — Secure Storage](https://optee.readthedocs.io/en/latest/architecture/secure_storage.html) — 安全存储设计
- [07-OP-TEE 架构与通信 — 07-optee-architecture.md](./07-optee-architecture.md) — 本文前置
- [optee-src/ta/trusted_keys/entry.c](./src/optee-src/ta/trusted_keys/entry.c) — HUK 加密/seal 完整示例
- [optee-src/ta/user_ta_header.c](./src/optee-src/ta/user_ta_header.c) — TA 头部生成模板
- [optee-src/lib/libutee/include/user_ta_header.h](./src/optee-src/lib/libutee/include/user_ta_header.h) — TA 标志位定义
- [optee-src/lib/libutee/include/tee_internal_api.h](./src/optee-src/lib/libutee/include/tee_internal_api.h) — Internal API 声明
- [optee-src/lib/libutee/tee_api_operations.c](./src/optee-src/lib/libutee/tee_api_operations.c) — 加密 API 实现
- [optee-src/lib/libutee/tee_api_objects.c](./src/optee-src/lib/libutee/tee_api_objects.c) — 存储 API 实现
- [optee-src/core/tee/tee_rpmb_fs.c](./src/optee-src/core/tee/tee_rpmb_fs.c) — RPMB 存储后端
- [optee-src/core/tee/tee_ree_fs.c](./src/optee-src/core/tee/tee_ree_fs.c) — REE FS 存储后端
- [optee-src/lib/libutee/include/pta_attestation.h](./src/optee-src/lib/libutee/include/pta_attestation.h) — 远程证明 PTA 接口

---

**下一篇**: [09-opensbi-riscv-counterpart.md](./09-opensbi-riscv-counterpart.md) — OpenSBI:RISC-V 版 TF-A
