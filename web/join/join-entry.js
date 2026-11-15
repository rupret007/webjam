const hostCamera = new URL(location.href).searchParams.get("cameras") === "1";
if (!hostCamera) {
  await import("./app.js");
  document.querySelector("[data-video-leave]")?.addEventListener("click", () => document.getElementById("leave").click());
} else {
  const [{ readInvite }, { RELAY_ORIGIN }, { VideoRoom }] = await Promise.all([
    import("./crypto.js"), import("./config.js"), import("./video-room.js"),
  ]);
  const root = document.getElementById("video-room");
  document.body.classList.add("camera-layout", "camera-host");
  document.title = "In-room camera · WebJam";
  let room;
  try {
    room = new VideoRoom({ root, origin: RELAY_ORIGIN, invite: readInvite(location.hash), host: true });
  } catch {
    root.hidden = false;
    root.dataset.videoState = "paused";
    root.querySelector("[data-camera]").hidden = true;
    root.querySelector("[data-video-status]").textContent = "This camera invitation expired. Open a fresh link from WebJam.";
  }
  window.addEventListener("pagehide", () => room?.close());
  root.querySelector("[data-video-leave]").addEventListener("click", () => {
    room?.pause("You left the camera.");
    if (!room) root.querySelector("[data-video-status]").textContent = "You left the camera.";
    root.querySelector("[data-video-leave]").disabled = true;
  });
}
