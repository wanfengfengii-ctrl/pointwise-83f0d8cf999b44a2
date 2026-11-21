# 北桥返修交班

北桥材料返修试验室的工艺员依据已核对的历史工单登记比例和耗时。夜班在站库关停后，按确切来件核定一批试片能在何时放行。班组长使用原定稿排待检工位，白班按报告号核对夜班实际采用的依据。

## 运行

使用 Python 3.11 标准库，在项目根目录运行。平台提供 `check_project.py`，执行 `tests` 包中的完整原回归。演示在独立的临时 SQLite 库运行，每次从同一份有限样例开始。

```sh
python3 -B check_project.py
python3 -B demo.py
```

演示中 A 用一小时转 B，B 再用一小时，其中一半放行。截止一小时为 0，截止两小时为 1/2，12 片的期望放行量为 6 片。B 勘误后实收新件，白班重开中心仍按原号取得原定稿及首执。演示输出的 `POINTWISE_SCENARIO` 行包含实际结果、独立预期和下游核账。

## Python 调用顺序

分别用 `sqlite3.connect` 打开站库和中心库。以 `RevisionStore(station_db).initialize()` 初始化站库，以 `PieceStore(center_db).initialize()`、`PlanStore(center_db).initialize()` 和 `ReportStore(center_db).initialize()` 初始化中心。初始化可以重复执行。写入入口自行管理提交，调用时应使用无待提交事务的连接。关闭连接由调用方负责。`demo.py` 展示了从样例登记到白班领取的完整调用。

1. `source.revisions.save_revision(RevisionStore(station_db), station, draft)` 返回整数修订号。`get_revision(store, station, revision_id)` 领取原修订。`RouteDraft` 包含 `PortDirectory`、各私有状态的 `Transition` 序列及非空工单依据。转移目标类别使用 `state` 或 `exit`。每个状态的出边为非负 `Fraction`，和恰为 1，耗时为非负整数小时。勘误按新修订登记。
2. `exchange.issue.issue_piece(store, station, revision_id)` 返回 `StationPiece`。用 `exchange.wire.encode_piece(piece)` 取得来件字节，用 `piece.identity` 取得声明的发行身份。同一冻结修订可重复发行相同来件。公开证据按入口、首次出口和耗时记录精确概率。每个入口保留 0 至 24 小时内的非零项，入口可以没有出站项。站内步骤和工单留站，窗口外或永久滞留的质量留在原始分母。
3. `exchange.receive.receive_piece(PieceStore(center_db), payload, declared_identity)` 返回已实收记录。用 `get_received_piece(store, received_id)` 领取。V1 载荷为 UTF-8 JSON，含 `version`、`station`、`revision`、`entries`、`exits`、`rows`。每行是 `[入口, 出口, 小时, 分子, 分母]`。身份为规范载荷字节的 SHA-256 十六进制值。入库前排序并规范化分数。匹配的等价来件重送返回原实收号。
4. `dispatch.plans.save_plan(PlanStore(center_db), piece_store, received_ids, bindings)` 返回不可变方案。`received_ids` 和 `bindings` 可给列表或元组。每条 `ExitBinding` 以 `ReceivedPort(received_id, port)` 指定出口，去向为 `Transfer(ReceivedPort(...))`、`Terminal.RELEASE` 或 `Terminal.REJECT`。每个出口恰绑一次，转派目标必须为所选件的真实入口。`get_plan(store, plan_id)` 读取原方案。改接通过新方案办理。
5. `analysis.requests.sign_report(report_store, plan_store, piece_store, ReportRequest(plan_id, start, deadline))` 返回 `ReportHandover`。截止为 0 至 24 的整数小时。每次到站按该入口重开站内流程，同时沿全程时钟扣除耗时。即时转派仍可继续，恰在截止达到放行也计入。概率为 `Fraction`，永久循环、拒收与超时的质量不重新分配。概率为 0 也有正式报告与首执。
6. `archive.retrieve.get_report(ReportStore(center_db), report_id)` 返回原报告和原首执。定稿保存所用方案、起点、时限、结果及各来件的实收号、发行身份和修订出处。按号领取仅查询定稿及首执。未知报告返回 `None`。

非法业务输入产生 `ValueError`。未知的有效格式修订号、实收号或方案号产生 `LookupError`。收件或接线失败不新增相应记录。签发的报告和首执共用一个 SQLite 事务，写入或提交失败沿原异常返回，两者一并回滚。
