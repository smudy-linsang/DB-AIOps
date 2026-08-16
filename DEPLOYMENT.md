# DB-AIOps v2.5 部署、升级与回退手册

> 适用版本：2.5.0
>
> 适用形态：单机 Compose 技术候选环境；银行生产应将元数据 PostgreSQL/TimescaleDB、Redis、Elasticsearch 和 TLS 入口替换为行内高可用服务。
>
> 配套文档：`V2.5_UPGRADE_DESIGN.md`、`V2.5_CODE_AUDIT_REMEDIATION_REPORT.md`、`V2.5_TEST_REMEDIATION_REPORT.md`

## 1. 放行边界

本手册可以完成可重复的 v2.5 部署、健康验证、迁移和应用回退，但不替代银行投产审批。以下证据未齐全时只能标记为“技术候选版”：

- 指定版本的达梦、GBase、TDSQL 实库矩阵；
- 500 实例容量、10,000 事件风暴、15 分钟压测和 2 小时长稳；
- IAM/MFA、CMDB、工单、WORM 审计和行内通知集成；
- 元数据、时序库和跨机房恢复演练；
- DBA、应用、信息安全、审计、运维平台五方 UAT 签字。

严禁在生产服务器执行 `runserver`、Vite dev server、运行时 `npm install`，也不得把明文数据库/Redis/Elasticsearch 端口暴露到生产网。

## 2. v2.5 进程拓扑

| 服务 | 作用 | 健康/主备语义 |
|---|---|---|
| `frontend` | Nginx 静态前端和 API 反向代理 | 仅绑定宿主机回环地址，外部由 TLS 入口代理访问 |
| `web` | 3 worker × 8 gthread Gunicorn API | `/livez` 仅进程存活；`/readyz` 校验依赖和后台角色 |
| `collector` | 指标采集 | 数据库租约选主，失租停止调度 |
| `sentinel` | ASH/异常检测与告警 | 数据库租约选主，失租停止调度 |
| `pipeline` | 事件、事故和诊断流水线 | 数据库租约选主，失租停止消费 |
| `timescaledb` | Django 元数据和时序数据 | Compose 为单实例，仅适合候选环境 |
| `redis` | 共享缓存、认证限流和事件流 | AOF；不可作为唯一业务事实来源 |
| `elasticsearch` | 检索与向量索引 | 开启账号认证；Compose 内部 HTTP 仅限隔离网络 |

应用容器均以非 root 用户运行，内部数据服务不发布宿主机端口。基础镜像和 Python 依赖均固定摘要/哈希。

## 3. 前置条件

- Linux 主机、Docker Engine 24+、Compose v2.24+；
- 候选环境建议至少 8 vCPU、16 GiB 内存、100 GiB 可用磁盘；
- 已分配正式域名和 TLS 证书，宿主机或行内负载均衡能代理到 `127.0.0.1:3000`；
- 出站网络仅放行被监控数据库、审批后的通知目标和经批准的模型服务；
- 服务器时间已同步，时区为 Asia/Shanghai；
- 生产密钥来自 Secret Manager/HSM，不通过聊天、工单正文或 Git 分发。

检查工具链：

```bash
docker version
docker compose version
git rev-parse HEAD
git status --short
```

部署必须使用已测试且工作区干净的提交。直推 `master` 的仓库在推送前必须先执行 `scripts/validate.sh all`。

## 4. 配置与密钥

在仓库根目录创建不纳入版本控制的 `.env.production`，权限必须为 `0600`。示例只列变量名，不提供可误用默认口令：

```dotenv
DJANGO_SECRET_KEY=<secret-manager-injected-value-at-least-50-chars>
DB_MONITOR_SECRET_KEY=<stable-encryption-key-at-least-32-chars>
POSTGRES_PASSWORD=<metadata-and-timeseries-password>
REDIS_PASSWORD=<redis-password>
ELASTIC_PASSWORD=<elasticsearch-password>
DJANGO_ALLOWED_HOSTS=db-aiops.example.bank
DJANGO_CSRF_TRUSTED_ORIGINS=https://db-aiops.example.bank

# 默认专用网段；若与宿主机现有 Docker 网络冲突，三项必须一起调整。
DBMONITOR_BACKEND_SUBNET=172.30.25.0/24
DBMONITOR_FRONTEND_BACKEND_IP=172.30.25.10
DBMONITOR_BIND_ADDRESS=127.0.0.1
DBMONITOR_HTTP_PORT=3000

# 可选：通知目标仍会经过 HTTPS/域名/端口校验。
WEBHOOK_ALLOWED_HOSTS=oapi.dingtalk.com,qyapi.weixin.qq.com
CONTENT_SECURITY_POLICY=default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'
```

生成随机密钥时使用行内密码系统。候选环境可由运维终端生成后立即导入 Secret Manager：

```bash
umask 077
openssl rand -base64 64
openssl rand -base64 48
```

重要约束：

- `DB_MONITOR_SECRET_KEY` 加密目标数据库密码，升级时必须保持稳定；丢失或误换会使已有凭据不可解密。
- `DJANGO_SECRET_KEY`、数据库、Redis、Elasticsearch 密码和 host/origin 缺失时，生产配置会拒绝启动。
- 不要把 `.env.production` 提交到 Git；部署后运行 `python scripts/scan_secrets.py` 复核。
- Compose 将可信代理限制到专用后端 CIDR。不要配置 `0.0.0.0/0` 或生产网大网段。

所有 Compose 命令均显式指定配置文件：

```bash
docker compose --env-file .env.production config --quiet
```

## 5. TLS 入口

Compose 默认把前端只绑定到 `127.0.0.1:3000`。必须由宿主机 Nginx、F5、Ingress 或行内负载均衡终止 TLS，并覆盖来源头：

```nginx
server {
    listen 443 ssl http2;
    server_name db-aiops.example.bank;

    ssl_certificate     /etc/pki/tls/certs/db-aiops.crt;
    ssl_certificate_key /etc/pki/tls/private/db-aiops.key;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 5s;
        proxy_read_timeout 120s;
    }
}
```

外层入口只允许受管网段访问，并执行 TLS 版本、密码套件、WAF、请求体大小和超时基线。不要把容器的 3000/8080 端口直接开放给终端用户。

## 6. 首次部署

### 6.1 构建与离线检查

```bash
scripts/validate.sh all
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production build --pull
```

行内环境应把构建产物推到内部镜像仓，执行 Trivy/Grype、签名和准入校验，再由生产主机按镜像 digest 拉取；生产主机不应临时访问公共镜像仓。

### 6.2 启动数据服务

```bash
docker compose --env-file .env.production up -d timescaledb redis elasticsearch
docker compose --env-file .env.production ps
```

三项必须为 healthy。失败时先检查对应服务日志，不要反复删除数据卷：

```bash
docker compose --env-file .env.production logs --tail=200 timescaledb
docker compose --env-file .env.production logs --tail=200 redis
docker compose --env-file .env.production logs --tail=200 elasticsearch
```

### 6.3 数据库迁移和应用启动

`web` 启动命令按顺序执行 Django migration、Timescale 幂等 schema 初始化、Playbook 缺失项初始化，然后启动 Gunicorn。模型迁移包含 `monitor.0029_processlease`。

```bash
docker compose --env-file .env.production up -d web
docker compose --env-file .env.production logs --tail=200 web
docker compose --env-file .env.production exec web python manage.py showmigrations monitor
docker compose --env-file .env.production exec web python manage.py check --deploy
```

`showmigrations` 中所有迁移必须为 `[X]`，`check --deploy` 不得有安全警告。随后启动三个后台角色和前端：

```bash
docker compose --env-file .env.production up -d collector sentinel pipeline frontend
docker compose --env-file .env.production ps
```

首次部署按双人复核流程建立平台管理员：

```bash
docker compose --env-file .env.production exec web python manage.py createsuperuser
```

管理员初次登录后立即建立实名 DBA、审计和只读账户；日常操作不得共用超级管理员。

## 7. 部署后 Smoke/SIT

先确认入口和安全跳转。下面的回环请求模拟 TLS 入口；正式验证应使用真实 HTTPS 域名：

```bash
curl --fail --silent --show-error \
  -H 'Host: db-aiops.example.bank' \
  -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:3000/livez

curl --fail --silent --show-error \
  -H 'Host: db-aiops.example.bank' \
  -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:3000/readyz
```

后台角色需要先获得租约并上报心跳，`/readyz` 才会返回 200。应在两个采集周期内就绪；超时必须按组件明细排障，不得关闭 `READINESS_REQUIRE_WORKERS` 换取绿灯。

最低验收清单：

1. `/livez` 返回 `status=alive` 和 `version=2.5.0`；
2. `/readyz` 返回 200，ORM、TSDB schema、Redis、collector、sentinel、pipeline 和 leader lease 均正常；
3. 未登录访问业务 API 返回 401，越权实例与不存在实例对外响应一致；
4. 登录失败限流能按真实客户端 IP 区分，不出现“不可信 XFF”告警；
5. 新增一个最小权限测试实例，连接测试、首轮采集和数据时效均正常；
6. 阻塞图在无数据时明确返回空/降级，不出现演示会话；
7. 中风险 Playbook 只生成待审批单，审批前不连接目标库；
8. 审计能回答操作者、实例、剧本、参数来源、审批、目标响应和最终状态；
9. 停止任一后台角色后 `/readyz` 在阈值内变为 503，恢复后重新变为 200；
10. 前端静态资源、深链接刷新、API 代理、CSP 和 401 处理正常。

查看租约和心跳：

```bash
docker compose --env-file .env.production exec web python manage.py shell -c \
  "from monitor.models import ProcessLease,ComponentHeartbeat; print(list(ProcessLease.objects.values())); print(list(ComponentHeartbeat.objects.values()))"
```

## 8. 版本升级

### 8.1 变更前检查与备份

记录当前提交、镜像 digest、迁移号和配置校验结果：

```bash
git rev-parse HEAD
docker compose --env-file .env.production images
docker compose --env-file .env.production exec web python manage.py showmigrations monitor
docker compose --env-file .env.production config --quiet
```

在任何升级前完成并验证：

- PostgreSQL/TimescaleDB 一致性备份和恢复点；
- Elasticsearch snapshot repository 快照；
- `.env.production` 与入口代理配置的加密备份；
- 目标数据库凭据解密抽检；
- 回退镜像 digest 和上一版代码可用。

元数据备份示例（备份文件写到受控备份目录，不写仓库）：

```bash
docker compose --env-file .env.production exec -T timescaledb \
  pg_dump -U postgres -d db_monitor -Fc > /secure-backup/db_monitor.pre-upgrade.dump
```

必须在隔离实例实际执行一次 `pg_restore --list` 和恢复演练，仅有 dump 文件不算备份成功。

### 8.2 升级顺序

```bash
scripts/validate.sh all
docker compose --env-file .env.production build --pull
docker compose --env-file .env.production up -d web
docker compose --env-file .env.production up -d collector sentinel pipeline frontend
docker compose --env-file .env.production ps
```

迁移采用向前兼容策略，旧后台角色与新 schema 的重叠窗口必须在设计文档中证明。单机 Compose 不是无损滚动部署；核心环境应由编排平台以 readiness 和 PodDisruptionBudget 完成滚动升级。

升级后重复第 7 节，并观察至少两个完整采集周期：采集新鲜度、事件队列、租约 token、通知失败率、API 5xx、数据库连接数和 TSDB 写入延迟。

## 9. 应用回退

### 9.1 触发条件

- 数据范围或鉴权异常；
- 错误成功审计、重复执行或错误目标；
- 迁移后核心查询不可用；
- 采集/告警/诊断在两个周期内无法恢复；
- 5xx、延迟、连接池或数据积压超过发布阈值。

### 9.2 回退原则

- 先禁止新的高风险执行和自动动作，再回退应用；
- v2.5 迁移是向前兼容的，应用回退时保留新表/列，不删除 `ProcessLease` 或覆盖已有 migration；
- 使用上一版已经签名的镜像 digest，不在事故中现场重建；
- 若数据被错误写入，另建修复脚本并保留审计，禁止手工无记录删除。

回退操作模板：

```bash
docker compose --env-file .env.production stop collector sentinel pipeline
# 将部署清单中的 backend/frontend image 改回已审批的上一版 digest
docker compose --env-file .env.production up -d web frontend
docker compose --env-file .env.production up -d collector sentinel pipeline
docker compose --env-file .env.production ps
```

回退后必须复验权限、`/readyz`、一个采集周期、告警、审批和审计。若问题来自不可向后兼容的数据变更，则停止业务写入并按已演练恢复点恢复，而不是尝试删除迁移文件。

## 10. 故障处理

### `/livez` 失败

检查容器退出原因、Gunicorn、磁盘只读/满、端口和入口代理。liveness 不依赖 PostgreSQL/Redis/TSDB；若失败说明 Web 进程本身不可响应。

### `/readyz` 返回 503

按响应中的组件检查：

- ORM：PostgreSQL 地址、认证、连接上限、migration；
- Timescale：schema 列、扩展、连接池和 statement timeout；
- Redis：密码、连接数、延迟和 AOF；
- worker/lease：对应容器日志、数据库时间、租约 holder、续租间隔；
- ES：索引检索可以降级，但必须在系统健康页显式展示。

不要通过扩大超时掩盖持续错误，也不要删除租约行强抢 leader；先确认旧 holder 已停止。租约自然过期或正常释放后，新的 fencing token 必须单调增加。

### 登录用户被共同限流

检查前端容器到 Web 的来源地址是否属于 `DBMONITOR_BACKEND_SUBNET`，以及 Nginx 是否覆盖 `X-Forwarded-For`。日志出现 `不在 TRUSTED_PROXY_IPS` 时应修正专用网段，禁止信任全网 CIDR。

### 被监控数据库不可用

确认纳管账号最小权限、DNS、TLS、只读属性和 statement timeout。所有采集连接必须通过 `DbConnector.get_connection(..., readonly=True)`；不能用提升权限的临时账号让监控“先跑起来”。

## 11. 日常运维与证据归档

- 每日检查采集新鲜度、未持有租约角色、通知失败、TSDB/ES/Redis 容量和证书到期；
- 每周复核高风险拒绝、审批分离、受保护账号命中和异常登录；
- 每月验证依赖漏洞、基础镜像、SBOM、恢复点和账号权限漂移；
- 每半年完成元数据、时序、Redis 丢失和机房切换演练；
- 每次发布归档提交号、镜像 digest、SBOM、迁移清单、测试报告、配置差异、审批人、执行人、观察结果和回退结论。

正式投产以 `V2.5_CODE_AUDIT_REMEDIATION_REPORT.md` 第 8 节和 `V2.5_UPGRADE_DESIGN.md` 第 14 节为最终放行清单；任何未验证项必须记录为已接受风险，不得用“未复现”替代关闭。
