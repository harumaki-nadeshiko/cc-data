#!/usr/bin/env python3
import unittest
from pathlib import Path


GEM5 = Path("/workspace/gem5")
CHI = GEM5 / "src/mem/ruby/protocol/chi"

REMOVED_FIELDS = (
    "ubcc_store_auth_valid",
    "ubcc_internal_writeback",
    "ubcc_store_requester",
    "ubcc_permission_epoch",
    "ubcc_write_disposition",
)


class DirtyReplacementOwnerDropContractTest(unittest.TestCase):
    def test_generic_chi_request_data_and_tbe_drop_writeback_sideband(self):
        for relative in ("CHI-msg.sm", "CHI-cache.sm", "CHI-cache-funcs.sm"):
            text = (CHI / relative).read_text(encoding="utf-8")
            for field in REMOVED_FIELDS:
                self.assertNotIn(field, text, f"{field} remains in {relative}")

    def test_replacement_uses_dbid_and_epbackend_owner_generation(self):
        actions = (CHI / "CHI-cache-actions.sm").read_text(encoding="utf-8")
        epsnf = (CHI / "ep/EPSNFController.cc").read_text(encoding="utf-8")
        backend = (CHI / "ep/EPBackend.cc").read_text(encoding="utf-8")
        slicc = (CHI / "CHI-cache-actions.sm").read_text(encoding="utf-8")
        config = (GEM5 / "configs/ruby/CHI_ubcc_framework.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("out_msg.usesTxnId := is_HN &&", actions)
        self.assertIn("tbe.snd_msgType == CHIDataType:NCBWrData", actions)
        self.assertIn("resolveWritePersistence", epsnf)
        self.assertNotIn("msg->m_fwdRequestor == msg->m_requestor", epsnf)
        self.assertIn("pending.ownerWriteback", epsnf)
        self.assertIn("handleWritebackWithMeta", epsnf)
        self.assertIn("UBWriteDisposition::DropOwner", epsnf)
        self.assertNotIn("PendingStoreCommit", backend)
        self.assertNotIn("_pendingStoreCommits", backend)
        self.assertNotIn("authorizeStoreCommit", backend)
        self.assertIn("hn_persistence_bridge.registerHnPersistence", slicc)
        self.assertIn("tbe.is_repl_tbe", slicc)
        self.assertIn("HnPersistenceBridge(ep_backend=ep_backend)", config)
        self.assertIn("hnf_cntrl.hn_persistence_bridge = bridge", config)
        self.assertIn("_pendingHnPersistence.find(key)", backend)
        self.assertIn("HnPersistenceKind::OwnerDrop", backend)
        self.assertIn("HnPersistenceKind::MemoryOnly", header :=
                      (CHI / "ep/EPBackend.hh").read_text(encoding="utf-8"))
        self.assertIn("requester->second.state == RequesterLineState::R_M", backend)
        self.assertIn("context.epoch = requester->second.epoch", backend)
        self.assertIn("requesterNode = _nodeId", backend)

    def test_owner_release_waits_for_confirmed_success(self):
        epsnf = (CHI / "ep/EPSNFController.cc").read_text(encoding="utf-8")
        backend = (CHI / "ep/EPBackend.cc").read_text(encoding="utf-8")
        start = backend.index("EPBackend::handleWritebackWithMeta")
        end = backend.index("EPBackend::commitStore", start)
        writeback = backend[start:end]

        self.assertIn("if (ok)", writeback)
        self.assertIn("it->second.state = RequesterLineState::R_I", writeback)
        self.assertNotIn("R_I", writeback[:writeback.index("if (ok)")])
        self.assertIn("[EP-WB-STALE-DROP]", epsnf)
        self.assertIn("dropStaleOwnerReplacement", epsnf)
        self.assertNotIn("ownerWritebackRejects", epsnf)
        self.assertNotIn("EP-WB-STALE-REJECT", epsnf)

    def test_ep_metadata_space_is_reported(self):
        header = (CHI / "ep/EPBackend.hh").read_text(encoding="utf-8")
        source = (CHI / "ep/EPBackend.cc").read_text(encoding="utf-8")
        self.assertIn("_requesterMetadataPeakEntries", header)
        self.assertNotIn("pendingStoreCommit", header)
        self.assertNotIn("PendingStoreCommit", header)
        self.assertIn("_pendingHnPersistencePeakEntries", header)
        self.assertIn("requester_line_entry_bytes", source)
        self.assertIn("[EPBACKEND-METADATA-PEAK]", source)

    def test_home_defers_original_owner_writeback_during_fill(self):
        controller = (Path("/workspace/modules/ubiomodule/UBCCController.cc")
                      .read_text(encoding="utf-8"))
        host = (Path("/workspace/modules/ubiomodule/ubio_main.cc")
                .read_text(encoding="utf-8"))
        self.assertIn("writebackMetadataPending", controller)
        self.assertIn("metadataDeferredWrites", host)
        self.assertIn("pendingDataRequests[key] = request", host)
        self.assertIn("const CoherenceMessage saved = request->second", host)
        self.assertIn("scheduleWritebackResponse(saved, keepAsClean)", host)
        self.assertIn("response.h.reqId = saved.h.reqId", host)
        self.assertIn("WritebackAdmission::Pending", controller)
        self.assertIn("WritebackAdmission::Stale", controller)
        self.assertIn("isLineBusy(line_pa)", controller)
        self.assertIn("[UBCC-WB-STALE]", controller)

    def test_matching_owner_drop_writeback_satisfies_active_recall(self):
        controller = (Path("/workspace/modules/ubiomodule/UBCCController.cc")
                      .read_text(encoding="utf-8"))
        start = controller.index("UBCCController::writebackAdmission(")
        end = controller.index("UBCCController::writebackMetadataPending", start)
        admission = controller[start:end]
        metadata = admission.index("_directory.fillPending(line_pa)")
        merge = admission.index("const bool recallOwnerDropMerge")
        busy = admission.index("if (isLineBusy(line_pa))")

        self.assertLess(metadata, merge)
        self.assertLess(merge, busy)
        self.assertIn("UBWriteDisposition::DropOwner", admission)
        self.assertIn("sourceSocket == _socketId", admission)
        self.assertIn("entry.state == MESIState::G_M", admission)
        self.assertIn("active->second.opType == OpType::RECALL", admission)
        self.assertIn("active->second.stage == OpStage::WAITING_TARGET_RESP", admission)
        self.assertIn("active->second.targetNode == requesterNode", admission)
        self.assertIn("active->second.baseEpoch", admission)
        self.assertIn("[UBCC-WB-RECALL-MERGE]", admission)

        validate_start = controller.index("UBCCController::validateWritebackPersistence")
        validate_end = controller.index("UBCCController::reserveWritebackPersistence",
                                        validate_start)
        validation = controller[validate_start:validate_end]
        self.assertIn("UBWriteDisposition::DropOwner", validation)
        self.assertIn("sourceSocket == _socketId", validation)
        self.assertIn("active->second.baseEpoch", validation)

        bridge = (CHI / "ep/HnPersistenceBridge.cc").read_text(
            encoding="utf-8")
        self.assertIn("if (!_backend)", bridge)
        self.assertIn("return;", bridge[bridge.index("if (!_backend)"):])

    def test_recall_commits_requester_state_only_after_proxy_completion(self):
        backend = (CHI / "ep/EPBackend.cc").read_text(encoding="utf-8")
        start = backend.index("EPBackend::handleRecallRequest")
        end = backend.index("EPBackend::sendRecallResponse", start)
        recall = backend[start:end]
        first_proxy = recall.index("_epRnfCtrl->startReadShared")

        self.assertNotIn("RequesterLineState::R_I", recall[:first_proxy])
        self.assertNotIn("RequesterLineState::R_S", recall[:first_proxy])
        self.assertEqual(recall.count("commitRecallRequesterState("), 2)

        commit_start = backend.index("EPBackend::commitRecallRequesterState")
        commit_end = backend.index("EPBackend::handleRecallRequest", commit_start)
        commit = backend[commit_start:commit_end]
        self.assertIn("recallMsg.dataNeeded && !capturedDataValid", commit)
        self.assertIn("requester->second.epoch != recallMsg.epoch", commit)
        self.assertIn("requester->second.state != RequesterLineState::R_M", commit)
        self.assertIn("RequesterLineState::R_S : RequesterLineState::R_I", commit)
        register_start = backend.index("EPBackend::registerHnPersistence")
        register_end = backend.index("EPBackend::completeHnPersistence",
                                     register_start)
        register = backend[register_start:register_end]
        self.assertIn("requester->second.state == RequesterLineState::R_M",
                      register)

    def test_ep_proxy_requests_backpressure_instead_of_unhandled_retry(self):
        cache = (CHI / "CHI-cache.sm").read_text(encoding="utf-8")
        actions = (CHI / "CHI-cache-actions.sm").read_text(encoding="utf-8")
        ports = (CHI / "CHI-cache-ports.sm").read_text(encoding="utf-8")
        transitions = (CHI / "CHI-cache-transitions.sm").read_text(
            encoding="utf-8"
        )
        eprnf = (CHI / "ep/EPRNFController.cc").read_text(encoding="utf-8")
        self.assertIn("AllocProxyRequest", cache)
        self.assertIn("action(AllocateTBE_ProxyRequest", actions)
        self.assertIn("check_allocate(storTBEs)", actions)
        self.assertIn("Event:AllocProxyRequest", ports)
        self.assertIn("reqInPort.recycle", ports)
        self.assertIn("AllocProxyRequest) {", transitions)
        self.assertIn("proxyOp == EpProxyOp_NoProxyOp", eprnf)
        self.assertIn("d.proxyOp == EpProxyOp_NoProxyOp", eprnf)
        self.assertIn("reliable_proxy", actions)
        self.assertIn("reqRdyPort.recycle", actions)
        self.assertIn("stall_and_wait(reqRdyPort, address)", actions)


if __name__ == "__main__":
    unittest.main()
