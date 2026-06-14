# SnpShared→EP-RNF 修复计划（基于 settled Phase A 决策）

## 0. 前提与结论

本计划**直接接受**以下已定案前提，不再重开讨论：

1. EP-RNF proxy special completion 正确语义：`CleanUnique/ReadUnique` 在 `epProxyOp != NoProxyOp` 时走 baseline prefix，完成阶段 `scrub_to_I`。
2. **强不变量**：EP-RNF **绝不能**成为稳定 `dir_owner`。
3. `ScrubEPRNF_ToI` 已存在且位置正确：`Final` 迁移中先 scrub，再 `Finalize_UpdateDirectoryFromTBE`。
4. `EpProxyOp` 语义已定：`NoProxyOp / InvalidateOnly / RecallUnique`。
5. `pickSharerForSnoop()` 排除了 EP-RNF，`Send_SnpShared` assert 只是 defense-in-depth。

因此，本次修复目标不是重新设计，而是把**“EP-RNF never becomes dir_owner”从最终 scrub 提前到 owner-assignment 点**，消除 `SnpShared→EP-RNF` 的上游来源。

---

## 1. Q1-Q4 收敛答案

- **Q1 = B**：采用强不变量，EP-RNF 永不成为 `dir_owner`。
- **Q2 = B**：`UpdateDirState_FromReqResp` 对 **proxy special completion** 应直接跳过目录更新，而不是仅“跳过 owner promotion、保留其它 mutation”。
- **Q3 = C**：修 owner assignment 点，而不是依赖末端 scrub 兜底。
- **Q4 = A**：上游修正验证通过后，`EPRNFController.cc` 中对 `SnpShared/SnpSharedFwd` 的 diagnostic `warn + SnpResp_SC` 应恢复为 fatal。

---

## 2. 根因定位

当前代码已做了一半：

- `CHI-cache-actions.sm:2654-2683` 的 `UpdateDirState_FromReqResp` 已对 `responder==EP-RNF` 做 owner-promotion guard；
- `CHI-cache-actions.sm:3502-3532` 的 `ScrubEPRNF_ToI` 会在最终提交前清空 owner/sharer/data。

但还不够强，原因有二：

### 2.1 `Finish_CleanUnique` 仍在制造“EP-RNF 将成为 exclusive owner”的意图

文件：`gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm:805-835`

当前普通路径里：

- `tbe.dataMaybeDirtyUpstream := true;`
- `tbe.requestorToBeExclusiveOwner := true;`
- `tbe.dir_ownerExists := false;`

这对 CPU RN-F / baseline `CleanUnique` 是对的；
但对 `requestor==epRnfMachineID && epProxyOp==InvalidateOnly` 是错的，因为该事务的 `Comp_UC` 只是 **completion token**，不是 owner grant。

### 2.2 `UpdateDirState_FromReqResp` 现在只“禁 owner 晋升”，但仍会做 `dir_sharers.add(responder)`

文件：`gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm:2654-2683`

当前顺序是：

1. `tbe.dir_sharers.add(in_msg.responder);`
2. 若 responder 是 EP-RNF，则跳过 owner promotion。

这意味着 proxy completion 的 `CompAck` 仍在参与目录 mutation，只是没走到最后一步。该行为与 `Q1=B` 不一致：

- proxy `CompAck` 不应承担任何 directory grant 语义；
- 它只表示“EP-RNF 本地 proxy 事务完成，可以退休”。

---

## 3. 具体修复方案

### 3.1 统一谓词

在 HN-F 侧用统一谓词识别 special completion：

```cpp
bool is_ep_proxy_special :=
    epRnfMachineVersion >= 0 &&
    tbe.requestor == tbe.epRnfMachineID &&
    tbe.epProxyOp != EpProxyOp:NoProxyOp;
```

注意：这里必须用 **requestor + epProxyOp** 判定，不能只看 `responder==EP-RNF`，否则会误伤普通 `ReadShared(shared_hint)` 相关完成路径。

### 3.2 修 `Finish_CleanUnique`（主修点）

文件：`gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`

对 `Finish_CleanUnique` 增加 special-case 分支：

- stale 分支保持现状；
- 非 stale 且 `is_ep_proxy_special` 时：
  - **不要**设置 `tbe.requestorToBeExclusiveOwner := true`；
  - **不要**设置 `tbe.dataMaybeDirtyUpstream := true`；
  - `tbe.dir_ownerExists := false`；
  - `tbe.requestorToBeOwner := false`；
  - `tbe.requestorToBeExclusiveOwner := false`；
  - `tbe.updateDirOnCompAck := false`；
  - 仍发送 `SendCompUCResp` + `WaitCompAck`；
  - 仍保留必要的本地收尾（如 `MaintainCoherence`，但不得再赋予 owner 语义）。

推荐伪 diff：

```diff
@@ action(Finish_CleanUnique)
   } else {
     assert(tbe.dir_sharers.count() == 1);
     assert(tbe.dataUnique);

-    tbe.dataMaybeDirtyUpstream := true;
-    tbe.requestorToBeExclusiveOwner := true;
-    tbe.dir_ownerExists := false;
+    if (epRnfMachineVersion >= 0 &&
+        tbe.requestor == tbe.epRnfMachineID &&
+        tbe.epProxyOp != EpProxyOp:NoProxyOp) {
+      tbe.dataMaybeDirtyUpstream := false;
+      tbe.requestorToBeOwner := false;
+      tbe.requestorToBeExclusiveOwner := false;
+      tbe.dir_ownerExists := false;
+      tbe.updateDirOnCompAck := false;
+    } else {
+      tbe.dataMaybeDirtyUpstream := true;
+      tbe.requestorToBeExclusiveOwner := true;
+      tbe.dir_ownerExists := false;
+    }
 
     tbe.actions.push(Event:SendCompUCResp);
     tbe.actions.push(Event:WaitCompAck);
     tbe.actions.push(Event:MaintainCoherence);
   }
 ```

### 3.3 强化 `UpdateDirState_FromReqResp`（副修点 / 保险）

文件：`gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`

把当前“EP-RNF responder 不晋升 owner”的弱 guard，升级为：

- 若 `is_ep_proxy_special`，则**整个 `CompAck` 不做任何目录更新**；
- 普通路径再执行 `dir_sharers.add()` 与 owner promotion。

推荐伪 diff：

```diff
@@ action(UpdateDirState_FromReqResp)
     if ((in_msg.type == CHIResponseType:CompAck) && tbe.updateDirOnCompAck) {
       assert(tbe.requestor == in_msg.responder);

-      tbe.dir_sharers.add(in_msg.responder);
-
-      bool is_eprnf_responder := epRnfMachineVersion >= 0 &&
-                                   in_msg.responder == tbe.epRnfMachineID;
-
-      if (tbe.requestorToBeOwner && !is_eprnf_responder) {
+      bool is_ep_proxy_special := epRnfMachineVersion >= 0 &&
+                                   tbe.requestor == tbe.epRnfMachineID &&
+                                   tbe.epProxyOp != EpProxyOp:NoProxyOp;
+
+      if (is_ep_proxy_special) {
+        // proxy CompAck is completion-only, not a directory mutation point
+      } else {
+        tbe.dir_sharers.add(in_msg.responder);
+        if (tbe.requestorToBeOwner) {
           ...
-      } else if (tbe.requestorToBeExclusiveOwner && !is_eprnf_responder) {
+        } else if (tbe.requestorToBeExclusiveOwner) {
           ...
+        }
       }
     }
 ```

这一步的意义是把语义从“禁止 EP-RNF 成为 owner”提升为“proxy completion 根本不是 directory update point”。

---

## 4. 为什么优先修 `Finish_CleanUnique`

因为 `Q3=C` 已定：应修 owner assignment 点，而不是继续加大 scrub 的职责。

`ScrubEPRNF_ToI` 仍然保留，作用是：

- 末端一致性保证；
- 保护 `Finalize_UpdateDirectoryFromTBE` / `Finalize_UpdateCacheFromTBE` 的断言；
- 防御未来别的路径再把 EP-RNF 带回目录元数据。

但**主语义**应前移到 `Finish_CleanUnique`：

- proxy `CleanUnique` 从一开始就不产生 owner intention；
- `CompAck` 从一开始就不承担目录提交语义。

---

## 5. 与 `ReadUnique(RecallUnique)` 的关系

本次文档聚焦 `SnpShared→EP-RNF`，直接触发点更像 `CleanUnique(InvalidateOnly)` 残留 owner 意图；但建议同时检查 `ReadUnique(RecallUnique)` 的 owner-assignment 点，确认同一不变量没有旁路：

1. 若 `UpdateDirState_FromSnpResp` / `UpdateDirState_FromSnpRespData` 在 recall 路径里把 `tbe.dir_owner := tbe.requestor` 且 `requestor==EP-RNF`，则最终虽会被 scrub 清掉，但仍违反 `Q3=C` 的设计方向。
2. 若本轮只修 `CleanUnique` 仍有 `Send_SnpShared assert`，下一检查点应转向 recall unique prefix 的 owner assignment。

也就是说：

- **第一刀**：`Finish_CleanUnique` + `UpdateDirState_FromReqResp`；
- **若仍复现**：再审 `ReadUnique(RecallUnique)` 的 `tbe.dir_owner := tbe.requestor` 赋值点。

---

## 6. 文件级改动清单

### 6.1 `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`

#### 必改

1. `Finish_CleanUnique`：
   - 增加 `is_ep_proxy_special` 分支；
   - special path 不设置 owner intent；
   - special path `updateDirOnCompAck := false`。

2. `UpdateDirState_FromReqResp`：
   - 由“EP-RNF responder guard”改为“exact special-completion guard”；
   - special path 直接跳过所有 directory mutation。

#### 保持不动

- `ScrubEPRNF_ToI`：不挪位置、不扩大职责；
- `Send_SnpShared` assert：保留；
- `pickSharerForSnoop()` 排除 EP-RNF：保留。

### 6.2 `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`

#### 验证后恢复

当前 `processSnoopImmediate()` 中：

- `SnpShared/SnpSharedFwd` 走 `warn + sendSnpRespSC()`（诊断模式）。

在上游修复稳定后，应改回 fatal：

```cpp
panic_or_fatal("EP_RNF should never receive SnpShared/SnpSharedFwd ...");
```

这对应 **Q4=A**。

---

## 7. 预期效果

### 7.1 直接效果

- HN-F 不再把 EP-RNF proxy `CleanUnique` 解释成“exclusive owner grant”；
- `CompAck` 不再把 EP-RNF 回写进 `dir_sharers/dir_owner` 的提交路径；
- `Send_SnpShared` 的 `dir_owner != epRnfMachineID` assert 不应再被触发。

### 7.2 间接效果

这会去掉一条错误的本地目录分支，但**不会单独解决**：

- TC2-4,6-8 的 Recall→Grant→Clear deadlock；
- TC5/TC11 的正式数据源为 0 问题。

即：本修复是**目录语义收敛**，不是 recall/data plane 总修复。

---

## 8. 验证矩阵

### A. 结构性验证

1. grep `Finish_CleanUnique`：确认 special path 不再设置 `requestorToBeExclusiveOwner`。
2. grep `UpdateDirState_FromReqResp`：确认 special path 直接跳过 `dir_sharers.add()` 和 owner promotion。
3. grep `EPRNFController.cc`：保留 diagnostic，待回归通过后再恢复 fatal。

### B. 运行时断言

建议新增 debug 断言：

```cpp
assert(!(epRnfMachineVersion >= 0 &&
         tbe.requestor == tbe.epRnfMachineID &&
         tbe.epProxyOp != EpProxyOp:NoProxyOp &&
         (tbe.requestorToBeOwner || tbe.requestorToBeExclusiveOwner)));
```

放置点：

- `Finish_CleanUnique` special 分支末尾；
- `Final` 迁移前或 `ScrubEPRNF_ToI` 开头。

### C. 测试回归

优先顺序：

1. 复现曾触发 `SnpShared→EP-RNF` 的用例；
2. TC11（因为同时覆盖 shared/unique 混合路径）；
3. TC2/3（观察是否仍只剩 Recall→Handshake 问题，而不是 owner 错误）；
4. 全量 TC2-TC11。

验收标准：

- 不再出现 `EP_RNF ... defensive SnpResp_SC` 日志；
- 不再出现 `Send_SnpShared` 的 EP-RNF owner assert；
- 若仍失败，失败类型应收敛到已知 recall/data-plane 问题。

---

## 9. 给 plan-designer 的可直接投喂 prompt

> 说明：当前环境未暴露 `task` 工具；以下是可直接用于 `task(subagent_type="plan-designer")` 的 prompt 内容。

```text
Phase C: Synthesize the final SnpShared→EP-RNF fix plan.

Read docs/recovery/scheme_v4.md and accept these settled decisions as final:
1. EP-RNF proxy special completion is baseline unique-flow prefix + completion scrub_to_I.
2. EP-RNF never becomes stable dir_owner.
3. ScrubEPRNF_ToI already exists and is correctly ordered before Finalize_UpdateDirectoryFromTBE.
4. EpProxyOp is fixed as NoProxyOp / InvalidateOnly / RecallUnique.
5. pickSharerForSnoop already excludes EP-RNF; Send_SnpShared assert is defense-in-depth.

Answer the prior convergence questions as:
- Q1=B
- Q2=B (proxy special completion should skip all directory mutation in UpdateDirState_FromReqResp)
- Q3=C
- Q4=A

Now synthesize a concrete code-fix plan focused on the remaining SnpShared→EP-RNF issue.
Check whether CHI-cache-actions.sm::UpdateDirState_FromReqResp and Finish_CleanUnique need additional guards for the EP-RNF proxy case.

Required conclusion:
- Main fix is to strengthen owner-assignment points, not scrub.
- Finish_CleanUnique must not create requestorToBeExclusiveOwner / dataMaybeDirtyUpstream for EP-RNF proxy CleanUnique.
- UpdateDirState_FromReqResp should use the exact special predicate (requestor==epRnfMachineID && epProxyOp!=NoProxyOp) and skip all directory mutation for proxy CompAck.
- ScrubEPRNF_ToI remains as final defense only.
- EPRNFController.cc diagnostic SnpShared/SnpSharedFwd fallback should be restored to fatal after upstream fix is validated.

Write the result to docs/recovery/snp_shared_fix_plan.md in Chinese, with:
1. Root cause
2. Exact file/line targets
3. Diff-style pseudocode
4. Validation matrix
5. Risks and non-goals
```
