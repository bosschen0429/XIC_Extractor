# Alignment

Alignment 根據樣本內的峰觀測與跨樣本證據，判斷哪些觀測可以對齊為同一個
Feature、哪些必須分開，以及哪些仍無法判定。

這是跨樣本對應問題，不等同於化合物鑑定。相同方法訊號、接近的 m/z 或 RT，
以及相似的局部峰形，都可能支持比較，但不能各自當成身分證明。

## 用語

| 用語 | 本文含義 |
| --- | --- |
| owner | 已建立的樣本內峰觀測，攜帶物理峰及局部方法證據。它是被對齊的對象，不是跨樣本對應已正確的保證。 |
| Feature | 根據證據接受的跨樣本觀測集合。程式中的 group 承載其成員與判斷結果，不另代表一種科學對象。 |
| pattern | 單一樣本中，有量測支持的局部 maxima 及其順序、間距、寬度與相對高度。Maxima 可以包含 shoulder，不必各自形成 owner。 |
| trajectory | 根據跨樣本對應連起的觀測位置。缺失樣本的位置可以由此估計，但估計值不是新觀測，也不是另一份 Feature 身分證據。 |

Method subject 表示採集或評估問題，不是 Feature 邊界。Target 的固定輸出列也不等於
跨樣本 Feature。共用術語仍由 [Domain Glossary](domain-glossary.md) 維護。

## 方法原則

### 觀測與對應

Owner 提供樣本內實際觀測；pattern 提供周圍結構。Alignment 使用這些資料：

- 提出值得比較的候選；
- 支持某個跨樣本對應；
- 排除與觀測或已接受限制不相容的對應。

提出候選不等於接受對應，有支持也不代表已排除其他合理解釋。已形成的 group
不能反過來製造其成員之間原本不存在的獨立證據。

Pattern 可以提出普通 RT／mass 搜尋未涵蓋的候選。因此，pattern 的作用不必限於
調整既有 edge 的分數。

### 未連接與分開

判讀結果時，區分下列證據含義；這不要求各自新增一種程式狀態：

1. 有足夠支持，可以接受對應。
2. 存在明確不相容證據，必須分開。
3. 尚未比較，或資料不足以建立連接。
4. 多個對應互相競爭或衝突，仍無法決定。

未找到不同之處，不能直接推出兩者相同；未找到足夠支持，也不能直接推出兩者不同。
RT 距離本身不構成身分或反證，搜尋範圍之外也不等於被排除。

### 對應必須共同成立

目前 Feature 的觀測單位，是每個 sample 至多一個被接受的物理峰觀測。
重複包裝同一觀測不增加支持，未解競爭也不能靠任意選取一個候選消除。

若接受 A ↔ B、A ↔ C早峰、B ↔ C晚峰，而 C 的早、晚峰是不能視為同一觀測的
兩個峰，這些對應就不能全部成立。

衝突本身不能指定哪一條跨樣本對應錯誤。局部峰的位置是直接觀測；把它與其他
sample 的峰連起來，仍是推論。證據足以排除其中一個對應時，撤回的是該對應及
依賴它的結論；不足時則保留未決，不因此否定整類證據。

局部 maxima、分割出的 segment 與可獨立接受的物理峰不能直接畫上等號。
使用它們作為分開證據時，必須符合上述觀測單位。

### 同一證據的不同用途

同一 pattern 可以參與候選生成、membership 判斷及位置估計，但不能因為在多個
步驟被使用，就被解讀成多份獨立確認。

- RT correction 改變比較座標，不改變量測到的峰位置或積分。
- Method compatibility 支持比較，不單獨證明同一 Feature。
- 已確認的 known-identity binding 是輸入限制，不由普通 pattern 相似性重新推導。
- 沒有可用校正或 pattern，不代表量測到零漂移或沒有峰。
- 不假設每個 ISTD 或未知 Feature 都遵循同一條 batch-wide monotonic RT drift。

### Backfill 與對應的區別

Backfill 在已有 Feature 脈絡下，對缺少可用觀測的 sample 進行有界搜尋。

預測搜尋位置、取得物理峰、判斷其是否屬於 Feature，以及決定是否輸出數值，是
不同問題。預測位置不能直接授予 membership 或方法支持；找到峰也不能直接授予
可寫出的定量值。

搜尋範圍與積分所需的訊號 context 分開。取得較寬 context，不表示可以接受原搜尋
範圍之外的候選。實際搜尋與接受條件見 [Backfill](backfill.md)。

## 目前實作

以下描述現行普通 Untargeted scalar CID-NL 路徑，不宣告這些算法、階段或門檻
不可替換。Known-identity constraints 與 composite subjects 保有各自的處理路徑。

### 輸入與比較範圍

Alignment 消費 Discovery 或 Targeted 提供的候選與 owner-backed hypotheses，
不建立第一筆 sample-local Discovery hit。RAW owner-build 只接受 MS1-feature-backed
Discovery candidates；review-only rows 不會因此取得第二次 XIC 搜尋。
輸入與排程細節見 [執行流程](execution-runtime.md#cid-nl-alignment-execution)。

相同 scalar neutral loss 的 non-composite method subjects，依 resolver intervals
及 known-reference comparison envelopes 形成相連 worksets。不同 loss、不相連 scope
及 composite subjects 維持各自 worksets。Workset 只決定比較範圍，不證明 membership，
也不合併各 subject 的 MS2 證據。

Confirmed known-ISTD owners 先依 exact identity label 分組。同 label 的 bindings
保持完整；unbound owners 不能只靠接近而加入。多 owner 綁到同 sample，或同 owner
綁到不同 labels，保留為 unresolved input conflict，不任意選取或拆開。

### 建立 owner 與局部 pattern

初始處理保留 owner，並另外取得較寬的 MS1 context。Context 中有量測支持的 maxima
可參與 pattern，即使它們不是獨立 owner 或定量結果；沒有足夠 smoothing-kernel
acquired support 的 apices 不納入。原 capture、physical evaluation 與積分保持不變。

目前 [pattern matching](../../xic_extractor/alignment/peak_trajectories.py)
保留峰順序，允許缺失與額外 maxima，使用共同 RT 位移與峰寬限制配對。
相對高度用於區分同樣完整的配對；不能唯一決定的 pattern 不直接提供對應。
Single-peak continuation 的實際影響另見[未解問題](#未解問題)。

同一峰可能因不同 seed windows 產生稍有不同的 apex。現行 doublet 判斷使用
較窄峰的寬度及 owner_apex_close_sec 下限，而非只看絕對 apex 或 seed RT 距離；
小於約半個峰寬的差異不因此被當成兩個峰而丟棄整個 component。

### 建立 trajectory

被接受的 pattern 配對連成 tracks，並綁定符合條件的 owners。
不同 tracks 不會只因沒有連接就被判定為不同。現行實作在同一 sample 觀測到
兩個 tracks 的不同 landmarks 時，建立兩者不相容的關係。

Trajectory 隨後用於普通 membership 的 seed、相容性判斷與 Backfill localization。
這些用途共享同一來源，不代表它們互相提供獨立驗證。

### 準備 RT 座標

Bootstrap 使用 directional grouping 的一致結果取得 batch-warp anchors；
普通 bootstrap membership 不直接作為最後答案。Pattern seeds 參與兩個方向；
方向間不一致的 extension 留在未決，不因此降級 seed。

Known-ISTD reference acquisition 沿用 Targeted 的正式選峰及 credible-anchor 判斷，
不另設一套「出現多個候選就全部不可用」的選峰規則。Imported reference workbook
保留其宣告的量測契約，不由 reader 補作 MS2 qualification。
CLI 輸入選擇見 [Untargeted 操作說明](../user/untargeted-discovery.md#alignment-cli-inputs)。

| 機制 | 現行來源與用途 |
| --- | --- |
| Known-ISTD drift lookup | Exact bindings 與有支持的 reference range，供 bootstrap comparison 使用；不更改 observed RT。 |
| BatchRtWarp | 完整 request 的 bootstrap groups；普通 anchor 至少 20 個同 cohort members，sample 至少 12 個 anchor observations。供 final grouping 與 Backfill 座標轉換。 |
| Peak trajectory | Ordered correspondences 與 direct／MS1-only positions；供 membership constraints 與 sample-local localization。 |
| Sample RT calibration diagnostic | 近乎全樣本都有的 features；只供 opt-in grouping experiments，不是 production warp。 |

Known-ISTD map 需要至少兩個 observed/reference 皆嚴格遞增的 landmarks。
缺失 landmarks 可在 canonical injection order 內插，不外插 order 或 RT；
duplicate order 停用 order interpolation。一般 map 不可用時，仍可使用既有
unique same-target supported-owner correction。詳細計算見
[drift evidence](../../xic_extractor/alignment/drift_evidence.py)。

Directional edge scoring 先評估 raw RT。Strong raw edge 通常不查 correction，除非
兩端有 direct、unique same-target binding；弱的非 RT 證據也不靠 correction 修補。
其餘符合條件的 pairs 使用預先準備的 map。Correction 若使差異惡化超過既有
10 秒門檻，或把 raw preferred-window 內的 pair 移到窗外，退回 raw-only 判斷。
既有 Targeted-ISTD product/loss exceptions 保留。這是
[edge scoring](../../xic_extractor/alignment/edge_scoring.py) 的現行政策，
不是所有 grouping 階段共用的規則。

Batch warp 完成後，final grouping 重新考慮普通 owners。沒有可用校正的樣本保留
observed RT，因此比較中可能混用 corrected 與 observed coordinates；這不代表
量測到零漂移。目前 raw-context 建立的 trajectory 會被後續 grouping 重用。
Residual neighbors 不跨 declared batch；空 batch ID 共用 unspecified cohort，
sample role 不定義 calibration cohort。Non-monotone sample mappings 不提供校正。

### 決定 membership

[Final grouping](../../xic_extractor/alignment/feature_membership_inference.py)
在相連的 mass neighborhoods 內建立候選，使用 RT、precursor 與 product mass 比較，
並處理每個 sample 的競爭。目前：

- 合格 trajectory seeds 在普通 edges 處理之前直接連接。
- 同 track 會影響 RT cost 與部分歧義處理。
- Pairwise precursor tolerance 限制候選；product deviation 是 soft cost，
  observed loss 不另作獨立 veto，累積 min-max envelope 不單獨否決 membership。
- Dense bands 使用自己的六個鄰近 injections 趨勢；sparse bands 使用較寬的
  warp-only RT 比較。這不是另一個 Backfill localization。
- 一個 band 不容納同 sample 的兩個 owners。
- 近似等價的競爭保留未決；其他獨立支持不因單一 contested pair 消失。
- 缺少 pair record 表示未評估，不表示不相容，也不由 group membership 補造正面 pair evidence。
- 最後普通 membership 不依賴輸入 sample 順序；singleton 或 unresolved competition
  保持 review-only，不將 assigned band 當成化學鑑定。

Bootstrap 的 seed support 使用 owner selected integration 的
height／baseline-residual MAD 與既有 MS2 seed count；沒有 baseline residual 的
路徑保留 Discovery-score fallback。CID_NL_ALIGNMENT_CONFIG 的現行
anchor_min_seed_events 為 1：MS1-only physical observation 計為一個事件，
不是套用 generic default 2。這不表示所有 final-window decisions 都套用
同一套 seed-rating gates。MS1-only Target owners 另用 precursor、physical quality
與 RT 證據；缺少 MS2 不單獨排除它們，也不因此加入 CID evidence group。

### 補接 groups 與處理 shoulders

普通 grouping 內部有 band bridge：在準備好的 warp 與完整 injection order 下，
相隔未觀測 injections 的 bands 可依 mass、corrected RT trend、sample disjointness
及 mutual unique-best 判斷連接。Injection gap 本身不是不同 Feature 的證據。

之後的 [sample-disjoint merge](../../xic_extractor/alignment/grouping_workflow.py)
以 preferred m/z tolerance、raw group-center RT 與 Backfill half-window 提出候選，
依距離組合，並檢查 sample overlap、trajectory compatibility 及 union RT trend。
Known-identity groups 保留自己的 membership。這與內部 bridge 的候選及競爭政策不同，
不能概括成「沒有 sample 分得開，所以合併」。

Shoulder rejection 是另一項處置。現行條件是兩組皆 established，較小組超過半數
members 與另一組共用 samples，且每個共同 sample 的 member 都有較小面積、
preferred m/z 相容與相同峰邊界。Rejected group 不規劃 Backfill，其 members
不搬入另一組。這是目前的 split-off shoulder 處理，不是一般 correspondence 合併規則。

### 定位與 Backfill

Trajectory 使用已觀測位置提供 localization；缺失位置則使用同 cohort 最多十二個
鄰近 observations，依可用 batch 座標與 robust local RT fit 估計。
這包含 compatible MS1-only positions，但不因此產生 CID-NL support。

不足的 temporal support、bounded extrapolation 與 localization reliability
各有明確處理；詳細條件由 [Backfill](backfill.md#query-rt-versus-group-rt) 維護。
目前 direct owner 會覆蓋同 sample 的借用位置；這不代表衝突已被解決，見下節。

Backfill 回傳觀測或無結果原因。原有 members 不因這次搜尋重新分配；
新觀測是否可接受，仍由 membership 與 cell decision 判斷處理。
輸出端消費完成的結果，不重新 grouping、選峰或積分。

## 未解問題

下列是方法說明與現行實作之間仍需釐清的問題，不是已決定的新算法或額外執行規則。

| 問題 | 已知情況 | 尚待釐清 |
| --- | --- | --- |
| Pattern 的接受條件 | 合格 seeds 直接連接，同 track 另影響 cost 與歧義處理。 | 哪些證據足以接受對應？後續步驟是在消費已接受的結果，還是在重複判斷？ |
| Single-peak continuation 的間接效果 | 不直接輸出其 owner／position，但仍參與 root contraction，可能改變其他 supported tracks。 | 此影響由什麼證據支持？它與「不能獨立建立 pattern」的邊界如何一致？ |
| 兩套 group 合併政策 | 內部 bridge 與後段 merge 使用不同候選、排序及競爭規則。 | 後段何時補足未評估候選，何時重判已有競爭？如何避免把未連接當成可直接合併？ |
| 借用位置與 direct owner 衝突 | Localization 以 direct owner 覆蓋同 sample 的借用位置，其他借用位置仍可能保留。 | 若兩者代表不同峰，哪些對應仍成立？只覆蓋 RT 是否掩蓋了尚未解決的衝突？ |
| Landmark 與物理峰的粒度 | 不同 landmarks 可形成 distinct-track 約束；landmarks 也可能包含 shoulders。 | 何時足以排除同一 Feature，何時只是同一峰內的結構或分割差異？ |
| 決策原因過度概括 | Assigned window groups 統一標示 RT trend 支持；少於 10 個 owners 的 union 可直接通過 trend 檢查。 | 說明如何區分實際量測到的支持、pattern seed，以及未被檢查排除？ |

這些問題不預先指定 pattern 或 RT／mass 永遠優先，也不預先要求單一 graph、
solver 或總分。其共同問題是具體對應及其依賴能否一起成立，而不是機制的名稱或數量。

## 證據與相關文件

目前實作的描述來自相應 source，不能當成科學正確性的獨立驗證。局部診斷只能支持
其實際比較的候選與資料範圍，不能外推為整套方法已驗證。Engineering tests 用於確認
已聲明的行為與回歸，不直接證明科學正確性。既有 retained fixtures 與解讀由
[Diagnostic Ledger](../diagnostic-ledger.md) 維護；
證據標示沿用 [Product Validation Contract](../agent/product-validation-contract.md)。

本文集中描述跨樣本對應，其他內容由既有文件維護：

- Group identity、disposition 與 lineage：[Group Boundary](family-hypothesis-boundary.md)。
- 有界搜尋與觀測接受：[Backfill](backfill.md)。
- 工作流程與結果介面：[Shared Processing Kernel](shared-processing-kernel.md)。
- 值、列與輸出契約：[Quant Matrix](quant-matrix.md)。
- 排程、cache 與失敗處理：[Execution Runtime](execution-runtime.md)。
- Sample/cohort 輸入：[Sample Metadata and QC](sample-metadata-qc.md)。
- 操作、診斷與檢閱：[Untargeted Discovery](../user/untargeted-discovery.md)、
  [Diagnostics Index](../../tools/diagnostics/INDEX.md)、
  [XIC Inspector](xic-inspector.md)。
