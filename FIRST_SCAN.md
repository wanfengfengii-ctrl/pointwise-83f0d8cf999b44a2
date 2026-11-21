# 北桥首扫条件放行

夜班在站库关闭后，用试片实际回传的首扫坐标核定准时放行率。V1 工作日流程、连接及初始化方式见 README.md。下面约定新增的 Python 调用、固定件交接及报告字段。

## 站端登记与固定件

`source.routes.RouteDraft(ports, transitions, work_order, scan_labels=...)` 接受从已有私有状态名到公开标签的映射，默认空映射。每个状态至多一个非空白字符串标签，多个状态可共用标签。标签按原字符串匹配。沿 `source.revisions.save_revision` 登记，通过 `get_revision` 可领回确切修订及标注。后续修改按新修订登记。使用原有 `Transition` 耗时和概率规则，每次到站从该入口的落点开始，落点及每次到达标注状态均触发扫码。到达时间是此前转移耗时之和。本站首扫仅指本次到站流程的第一条扫码。

`exchange.issue.issue_piece(store, station, revision_id, version=2)` 使用确切修订发行 V2。同一修订重领得到同一规范字节及身份。原三参调用仍发行 V1，V1 编码和首次出站口径保持兼容。

V2 用 `exchange.wire.encode_piece(piece)` 取得 UTF-8 JSON 字节，`piece.identity` 为规范字节的 SHA-256 十六进制身份。V2 顶层含 `version`（整数 2）、`station`、`revision`、`entries`、`exits` 和 `evidence`。端口、站号及确切修订沿用原含义。`evidence` 的内部表示由实现选择，并在这里补充其字段、规范化和校验约定。概率采用精确有理数。空扫码或空出站证据的合法入口仍保留在端口目录。

每个入口的证据须足以区分未扫码出站、扫码后出站、已扫码但至第 24 小时仍未出站。对一次到站，必须能按本站首扫标签及时点取得其支持质量，并保留它与实际首次出口及出站耗时的联合关系。首扫支持覆盖扫码后永久滞留或超出取证窗口才出站的路径。扫码时刻及出站耗时均相对于本次入口落点，第 0 和第 24 小时均计入窗口。私有状态名、私有转移及工单留站。

按原 `exchange.receive.receive_piece(piece_store, payload, declared_identity)` 独立实收 V2，`get_received_piece` 按号读取。验收检查版本、来源、端口、时间、精确概率及证据内部的一致性。各入口质量不得超过 1，任何首扫下的联合出站质量不得超过该首扫的支持质量，出站时间不得早于对应首扫。声明身份须与规范化后的正文一致。仅改变 JSON 空白或对象键顺序的等价重投仍取原实收号，身份冲突或坏件拒收。用原 `dispatch.plans.save_plan` 绑定实收号和出入口，保存冻结方案。

## 闭站签报与次班领取

`analysis.requests.ScanReportRequest(plan_id, start, deadline, scan_received_id, scan_label, scan_hour)` 中，`start` 是方案内的 `ReceivedPort` 入口。截止和观测小时都是整数，包含 `0 <= scan_hour <= deadline <= 24` 的两个端点，布尔值无效。`scan_received_id` 必须是该方案选定的 V2 实收件号，`scan_label` 是非空白字符串。该实收件没有此标签的命中时仍可签无证据。

`analysis.requests.sign_scan_report(report_store, plan_store, piece_store, request)` 只依赖中心库。方案选定件都须为 V2。保持同一全程时钟，按实际接线路径判断首扫是否精确匹配实收号、标签和绝对小时。全程首扫之前发生的任何扫码都会排除更晚的目标扫码，同小时以事件的经过顺序区分。首扫匹配后，继续按原接线及剩余时间核定放行，后续扫码不改该次匹配。恰在截止放行计入。即时回路允许任意有限次经过后产生观测或放行，闭类质量保持原权重。匹配后永久滞留、拒收或超时仍计入支持。

签发返回交班对象，含 `report` 和 `first_receipt`。`report` 含 `report_id`、完整的 `request`、`sources` 及三项结果。`sources` 沿 V1 的 `PieceOrigin` 口径记录该方案实际所选各件的实收号、发行身份、站号和修订。`support` 是全程首扫匹配的概率，`joint` 是匹配且准时放行的概率，均为 `Fraction`。`conditional` 在支持为正时是 `joint / support` 的 `Fraction`，支持为零时为 `None`。有支持而无准时放行时交付精确零。

`archive.retrieve.get_scan_report(report_store, report_id)` 返回同样的交班对象。签发与首执同时落库，写入或提交失败无本次新增，可重试，沿用原 SQLite 异常。中心重开或另收勘误件后按原号领取存值，领取依赖定稿和首执。沿用 `ReportStore.initialize()` 初始化方式。

坏件、错配、混版和非法请求报 `ValueError`。合法格式的未知修订、实收或方案报 `LookupError`，例如存在但未选中的扫码实收件属于错配。有效格式的未知首扫报告号返回 `None`。编号范围沿用 V1。V1 的调用、站件、旧报告和回归保持兼容。

## 手算场景

`samples/first_scan.json` 与原工作日样例使用相同的路线字段。三条 `versions` 各自冻结一份共用路线，应用各自的 `scan_labels`。按各自实收号使用 `in` 起步，将 `pass` 接到 `Terminal.RELEASE`。固定 `observation` 的标签、绝对小时和截止。`expected` 依次为 `support`、`joint`、`conditional`，分数写成字符串，空条件写成 JSON 的 `null`。把 R 从滞留支路挪到放行支路时，支持仍为 1/2，条件率从 0 变成 1。撤掉 R 后支持为 0。`python3 -B check_scan_fixture.py` 核对这三版在 V1 口径上的相同出口证据和总体放行率。

同小时竞争可用两站确定性路线核验。A 入口扫 X，零小时转 B，B 入口扫 R 并立即放行。以 B 件的第 0 小时 R 为条件，支持和联合均为 0，条件为空。去掉 A 的 X 并把 A 转 B 改为一小时，到第 1 小时以 B 件的第 1 小时 R 为条件，三项均为 1。

即时回路可用 A 每次入站后以 1/2 即时转 B 并由 B 无扫码立即转回 A、以 1/4 进入本站无扫码闭类、以 1/4 即时到达 R 并在一小时后放行的路线。以 A 件第 0 小时的 R 为条件，截止 0 小时签 `(1/2, 0, 0)`，截止 1 小时签 `(1/2, 1/2, 1)`。

## 交付运行

根目录 `.pointwise-runtime.json` 沿用现有版本 1 的 `cli` 清单。`setup` 是准备命令的参数数组列表，可为空。`verify` 和 `demo` 各是一个命令的参数数组。保留原回归及工作日演示，并接通首扫核验和演示。核验比较实际业务结果与手算预期，偏差以非零退出。演示展示站库关闭后签报，并在中心重开后按原号核对定稿及首执。
