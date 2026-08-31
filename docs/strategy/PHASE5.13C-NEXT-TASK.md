# Phase 5.13C Next Task

Phase 5.13C-A1 已补齐 metadata-only local asset registration、双角色工作台、
中央 mirroring 与正式浏览器验收。Phase 5.13D 尚未启动。

Before planning a later execution stage:

1. update legacy PostgreSQL schema tests to assert required minimum migration or
   schema objects rather than an obsolete exact head;
2. define an explicit policy-bundle approval boundary;
3. define central-to-hospital execution-order authorization without path access;
4. retain `hard_isolation=false` until a separately accepted isolation stage.

下一轮可规划 Phase 5.13D 的签名 PolicyBundle 与 ExecutionOrder 指令控制；
该阶段仍不得运行模型、读取原始数据或产生 Artifact。
