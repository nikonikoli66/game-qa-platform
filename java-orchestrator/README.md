# Java 任务编排服务（可选加速层）

> Spring Boot 3.3 / Java 17+。**不启动它，整个项目照样跑**（Python 单栈是默认路径）；它是"多 QA 并发提交、几百条用例同时跑"时才需要的调度层。

## 为什么这个模块用 Java

不是因为偏好 Java，而是因为分工真的不同：

| 关注点 | 归属 | 理由 |
|---|---|---|
| 用例生成、AI 调用、数据分析 | Python | AI/数据生态在这边（pandas、LLM SDK、pytest） |
| **并发调度、任务状态机、幂等、取消** | **Java** | 线程池、事务、强类型、Spring 生态更适合做"服务端调度" |
| 契约 | HTTP + JSON | 语言无关，将来换 Go/Node 写执行器也能对接 |

一句话：**Python 负责"怎么测"，Java 负责"同时测很多、且不出错"。**

## 它解决什么问题

单机串行跑 15 条用例没问题；但 10 个 QA 同时点"执行"、每人 50 条用例时，会立刻暴露：

- **并发控制**：不能无限开进程，要有队列和上限（`concurrency: 3`）；
- **状态一致性**：任务不能出现"既在跑又已完成"，所以用状态机 + DB 级 CAS；
- **幂等**：CI 重试、用户连点，同一请求不能执行两遍；
- **可取消**：提交错了要能停下来；
- **可观测**：任何时候都能查到"这批跑到哪了、哪条挂了、为什么"。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/batches` | 提交批次 `{caseIds:[...], clientRequestId:"..."}` → `202` + `batchId` |
| GET | `/api/v1/batches/{id}` | 进度：total/passed/failed/canceled/running/pending/progress |
| GET | `/api/v1/batches/{id}/tasks` | 每条任务的状态、失败分类、耗时、runId |
| POST | `/api/v1/batches/{id}/cancel` | 取消所有 PENDING 任务（RUNNING 不打断） |
| GET | `/api/v1/batches?limit=10` | 最近批次 |
| GET | `/api/v1/health` | 自身 + 下游执行器健康状态 |

## 三个关键设计

**1. DB 级 CAS 抢占，而不是应用层加锁**

```sql
UPDATE qa_task SET status='RUNNING', started_at=? WHERE id=? AND status='PENDING'
```
返回 0 就说明没抢到（已被取消或已被别的线程执行）。这是"同一任务绝不跑两遍"的真正保证，多实例部署也成立。

**2. 事务提交后才派发**

用 `TransactionSynchronization.afterCommit()` 派发任务。如果不等提交就丢进线程池，异步线程可能查不到刚插入的任务行，表现为"任务莫名丢失"——这个坑很隐蔽。

**3. 状态机写在枚举里**

`TaskStatus.canTransitTo()` 定义合法迁移，`PENDING→PASSED` 这种跳级会被拒绝。有 6 条单测覆盖（`mvn test`）。

## 跑起来

```bash
# 方式一：一键脚本（自动编译、起执行器、跑并发/幂等/取消三项验证）
python scripts/demo_java_orchestrator.py

# 方式二：手工
cd java-orchestrator && mvn package -DskipTests
java -jar target/game-qa-orchestrator-1.0.0.jar --server.port=18080
# 另开终端：python -m uvicorn app.worker_api:app --port 8100
curl -X POST http://127.0.0.1:18080/api/v1/batches \
  -H "Content-Type: application/json" \
  -d '{"caseIds":["<caseId>"],"clientRequestId":"manual-1"}'
```

## 端口说明

- 编排服务默认 **18080**（不是 8080：开发机上 8080 常被代理/其它服务占用，本机实测直接返回 502）
- 执行器默认 **8100**，可通过 `ORCH_PORT` 环境变量或 `--server.port` 覆盖

## 已知限制

- 存储用 H2 内存库：重启即清空，生产换 MySQL/PostgreSQL 只改 `application.yml`；
- 任务中断后不会自动恢复（无持久化队列），生产应换 RabbitMQ / Kafka / Redis Stream；
- 不做失败重试：重试语义属于执行层（Python 侧自愈），调度层重试会放大故障。
