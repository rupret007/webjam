"""Process-provenance and stop/start race tests for Jamulus RPC monitoring."""

from __future__ import annotations

import dataclasses
import threading
import unittest
from unittest.mock import MagicMock, patch

from core.jamulus_rpc_client import (
    ChannelInfo,
    JamulusRpcClient,
    JamulusRpcMonitorIdentity,
)
from jamulus_controller import JamulusController


class TestRpcMonitorEpoch(unittest.TestCase):
    @staticmethod
    def _start_without_network(
        client: JamulusRpcClient,
        *,
        generation: int,
        pid: int,
    ) -> JamulusRpcMonitorIdentity:
        with patch.object(client, "_run_loop", return_value=None):
            identity = client.start(
                process_generation=generation,
                process_id=pid,
            )
            thread = client._thread
            if thread is not None:
                thread.join(timeout=1.0)
        return identity

    def test_snapshot_is_available_only_for_exact_positive_process(self):
        client = JamulusRpcClient(port=22222)
        identity = self._start_without_network(
            client,
            generation=7,
            pid=4312,
        )
        try:
            snapshot = client.monitor_snapshot_for(
                process_generation=7,
                process_id=4312,
            )
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.identity, identity)
            self.assertTrue(snapshot.running)
            self.assertFalse(snapshot.available)
            self.assertIsNone(snapshot.last_activity_at)
            self.assertIsNone(snapshot.last_activity_age_seconds)
            self.assertIsNone(
                client.monitor_snapshot_for(
                    process_generation=8,
                    process_id=4312,
                )
            )
            self.assertIsNone(
                client.monitor_snapshot_for(
                    process_generation=7,
                    process_id=4313,
                )
            )
            self.assertIsNone(
                client.monitor_snapshot_for(
                    process_generation=0,
                    process_id=4312,
                )
            )
        finally:
            client.stop()

    def test_old_reader_after_new_start_cannot_stamp_or_publish(self):
        updates: list[tuple[list[ChannelInfo], JamulusRpcMonitorIdentity]] = []
        client = JamulusRpcClient(
            port=22222,
            on_participants_changed_with_source=(
                lambda participants, source: updates.append(
                    (participants, source)
                )
            ),
        )
        old = self._start_without_network(client, generation=1, pid=1001)
        client.stop()
        current = self._start_without_network(client, generation=2, pid=1002)
        try:
            self.assertTrue(client._stamp(current.monitor_epoch))
            before = client.monitor_snapshot()

            client._dispatch_obj(
                {
                    "jsonrpc": "2.0",
                    "method": "jamulusclient/clientListReceived",
                    "params": {"clients": [{"id": 9, "name": "Old process"}]},
                },
                epoch=old.monitor_epoch,
            )

            after = client.monitor_snapshot()
            self.assertEqual(after.identity, current)
            self.assertEqual(after.last_activity_at, before.last_activity_at)
            self.assertEqual(updates, [])
            self.assertEqual(client.get_channel_clients(), None)

            client._update_clients(
                [{"id": 3, "name": "Current process"}],
                epoch=current.monitor_epoch,
            )
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0][1], current)
        finally:
            client.stop()

    def test_old_response_cannot_consume_new_epochs_same_request_id(self):
        client = JamulusRpcClient(port=22222)
        old = self._start_without_network(client, generation=1, pid=1101)
        client.stop()
        current = self._start_without_network(client, generation=2, pid=1102)
        try:
            with client._lock:
                client._inflight[1] = (
                    current.monitor_epoch,
                    "jamulusclient/getClientList",
                )
            client._dispatch_obj(
                {"jsonrpc": "2.0", "id": 1, "result": {"clients": []}},
                epoch=old.monitor_epoch,
            )
            with client._lock:
                self.assertEqual(
                    client._inflight[1],
                    (
                        current.monitor_epoch,
                        "jamulusclient/getClientList",
                    ),
                )
        finally:
            client.stop()

    def test_stopped_monitor_cannot_resurrect_state_or_callbacks(self):
        callbacks: list[str] = []
        client = JamulusRpcClient(
            port=22222,
            on_chat=callbacks.append,
        )
        identity = self._start_without_network(
            client,
            generation=4,
            pid=2004,
        )
        client.stop()

        client._dispatch_obj(
            {
                "jsonrpc": "2.0",
                "method": "jamulusclient/chatTextReceived",
                "params": {"chatText": "late"},
            },
            epoch=identity.monitor_epoch,
        )
        client._update_clients(
            [{"id": 1, "name": "late"}],
            epoch=identity.monitor_epoch,
        )

        snapshot = client.monitor_snapshot()
        self.assertFalse(snapshot.running)
        self.assertFalse(snapshot.available)
        self.assertFalse(snapshot.authenticated)
        self.assertIsNone(snapshot.last_activity_at)
        self.assertEqual(callbacks, [])
        self.assertIsNone(client.get_channel_clients())

    def test_stop_waits_for_inflight_callback_then_closes_epoch(self):
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()

        def callback(_text: str) -> None:
            entered.set()
            release.wait(timeout=2.0)

        client = JamulusRpcClient(port=22222, on_chat=callback)
        identity = self._start_without_network(
            client,
            generation=5,
            pid=2005,
        )
        caller = threading.Thread(
            target=lambda: client._emit_chat(
                "current",
                epoch=identity.monitor_epoch,
            )
        )
        caller.start()
        self.assertTrue(entered.wait(timeout=1.0))

        stopper = threading.Thread(
            target=lambda: (client.stop(), stopped.set())
        )
        stopper.start()
        self.assertFalse(
            stopped.wait(timeout=0.05),
            "stop returned while an epoch callback was still executing",
        )
        release.set()
        caller.join(timeout=1.0)
        stopper.join(timeout=1.0)
        self.assertFalse(caller.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(stopped.is_set())

        # Once stop has returned, the retired epoch cannot invoke again.
        client._emit_chat("late", epoch=identity.monitor_epoch)

    def test_chat_and_recorder_callbacks_carry_exact_monitor_identity(self):
        chat: list[tuple[str, JamulusRpcMonitorIdentity]] = []
        recorder: list[tuple[bool, int, JamulusRpcMonitorIdentity]] = []
        client = JamulusRpcClient(
            port=22222,
            on_chat_with_source=lambda text, source: chat.append((text, source)),
            on_recorder_state_with_source=(
                lambda recording, state, source: recorder.append(
                    (recording, state, source)
                )
            ),
        )
        identity = self._start_without_network(
            client,
            generation=6,
            pid=2006,
        )
        try:
            client._emit_chat("current", epoch=identity.monitor_epoch)
            client._emit_recorder_state(3, epoch=identity.monitor_epoch)

            self.assertEqual(chat, [("current", identity)])
            self.assertEqual(recorder, [(True, 3, identity)])
        finally:
            client.stop()

    def test_monitor_identity_is_immutable(self):
        identity = JamulusRpcMonitorIdentity(1, 2, 3)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            identity.process_id = 99  # type: ignore[misc]


class TestControllerParticipantProvenance(unittest.TestCase):
    @staticmethod
    def _controller() -> JamulusController:
        controller = JamulusController(
            host="127.0.0.1",
            port=22124,
            rpc_port=22222,
        )
        controller.settings.host_server_enabled = False
        controller.rpc_client = MagicMock()
        controller.protocol = MagicMock()
        controller.audio_engine = MagicMock()
        controller.identity_callbacks = []
        controller.running = True
        return controller

    def test_stale_participant_source_is_rejected_not_relabeled(self):
        controller = self._controller()
        current = JamulusRpcMonitorIdentity(22, 8, 8008)
        old = JamulusRpcMonitorIdentity(19, 7, 7007)
        controller._rpc_monitor_identity = current
        received = MagicMock()
        controller.register_identity_callback(received)

        controller._on_rpc_participants_with_source(
            [ChannelInfo(channel_id=1, name="Old", is_local=True)],
            old,
        )
        received.assert_not_called()
        self.assertEqual(controller.get_participants(), [])

        controller._on_rpc_participants_with_source(
            [ChannelInfo(channel_id=2, name="Current", is_local=True)],
            current,
        )
        received.assert_called_once()
        participants, source = received.call_args.args
        self.assertEqual(source, current)
        self.assertEqual([p.name for p in participants], ["Current"])

    def test_identity_callback_receives_detached_participants(self):
        controller = self._controller()
        source = JamulusRpcMonitorIdentity(31, 9, 9009)
        controller._rpc_monitor_identity = source
        received: list[tuple[list, JamulusRpcMonitorIdentity]] = []
        controller.register_identity_callback(
            lambda participants, identity: received.append(
                (participants, identity)
            )
        )

        controller._on_rpc_participants_with_source(
            [ChannelInfo(channel_id=2, name="Original", is_local=True)],
            source,
        )
        controller.participants[2].name = "Later process state"

        self.assertEqual(received[0][0][0].name, "Original")
        self.assertEqual(received[0][1], source)

    def test_recorder_and_chat_reject_replaced_monitor_identity(self):
        controller = self._controller()
        current = JamulusRpcMonitorIdentity(41, 12, 12012)
        old_epoch = JamulusRpcMonitorIdentity(40, 12, 12012)
        old_process = JamulusRpcMonitorIdentity(39, 11, 11011)
        controller._rpc_monitor_identity = current
        recorder = MagicMock()
        chat = MagicMock()
        controller.recorder_state_callback_with_source = recorder
        controller.chat_callback_with_source = chat

        controller._on_rpc_recorder_state_with_source(True, 3, old_epoch)
        controller._on_rpc_chat_with_source("old epoch", old_epoch)
        controller._on_rpc_recorder_state_with_source(True, 3, old_process)
        controller._on_rpc_chat_with_source("old process", old_process)

        recorder.assert_not_called()
        chat.assert_not_called()

        controller._on_rpc_recorder_state_with_source(True, 3, current)
        controller._on_rpc_chat_with_source("current", current)

        recorder.assert_called_once_with(True, 3, current)
        chat.assert_called_once_with("current", current)

    def test_controller_start_forwards_exact_process_identity(self):
        controller = self._controller()
        controller.running = False
        identity = JamulusRpcMonitorIdentity(41, 12, 12012)
        controller.rpc_client.start.return_value = identity
        controller.rpc_client.available = True
        try:
            result = controller.start(
                process_generation=12,
                process_id=12012,
            )
            self.assertEqual(result, identity)
            controller.rpc_client.start.assert_called_once_with(
                process_generation=12,
                process_id=12012,
            )
        finally:
            controller.stop()

    def test_start_does_not_deadlock_with_old_callback_needing_lifecycle(self):
        """Reproduce lifecycle -> old-callback -> lifecycle lock inversion.

        The old callback re-entrantly stops its own RPC epoch, leaving its
        callback token entered, then waits for the controller lifecycle lock.
        A replacement start already owns that lifecycle lock. It must use
        provenance instead of waiting for the old token or both threads
        deadlock.
        """

        client = JamulusRpcClient(port=22222)
        controller = self._controller()
        controller.running = False
        controller.rpc_client = client
        controller.protocol = MagicMock()
        controller.audio_engine = MagicMock()
        lifecycle_held = threading.Event()
        old_stopped = threading.Event()
        replacement_finished = threading.Event()

        with patch.object(client, "_run_loop", return_value=None):
            old = client.start(process_generation=51, process_id=5101)

            def old_callback() -> None:
                self.assertTrue(lifecycle_held.wait(timeout=1.0))
                token = client._begin_epoch_callback(old.monitor_epoch)
                self.assertIsNotNone(token)
                client.stop()  # does not wait for this callback's own token
                old_stopped.set()
                try:
                    with controller._lifecycle_guard():
                        pass
                finally:
                    client._end_epoch_callback(token)

            callback_thread = threading.Thread(
                target=old_callback,
                daemon=True,
            )
            callback_thread.start()

            def replacement_start() -> None:
                with controller._lifecycle_guard():
                    lifecycle_held.set()
                    self.assertTrue(old_stopped.wait(timeout=1.0))
                    controller.start(
                        process_generation=52,
                        process_id=5202,
                    )
                replacement_finished.set()

            start_thread = threading.Thread(
                target=replacement_start,
                daemon=True,
            )
            start_thread.start()
            self.assertTrue(
                replacement_finished.wait(timeout=1.0),
                "replacement start deadlocked draining an old callback",
            )
            start_thread.join(timeout=1.0)
            callback_thread.join(timeout=1.0)
            self.assertFalse(start_thread.is_alive())
            self.assertFalse(callback_thread.is_alive())
            controller.stop()


if __name__ == "__main__":
    unittest.main()
