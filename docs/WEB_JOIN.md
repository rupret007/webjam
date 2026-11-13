# Web Join invite and listen-only entry

An invited person opens the link and taps **Join**. That tap does not ask for a
microphone, a microphone check, or a headphones confirmation. The session
starts listen-only. After the room is connected, **Use microphone** and the
headphones note are optional. The headphones note is there to avoid echo and
feedback. Invite expiry, enrollment, and the fresh-invite recovery are unchanged.

The host sends an invite that is already prepared. **Share invite** calls the
platform share sheet from that click. When share is unavailable, **Email invite**
is the primary action and opens a mail message. **Copy invite** stays available
and is not the primary action. A timer or a fetch callback must not call share.

Choosing a known Game Night game still opens the protected page with
`deliver=share`. A pasted guest link or room address does not gain `deliver`.
