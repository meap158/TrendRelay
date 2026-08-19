"""TrendRelay's own workspace, reachable by an outside assistant over MCP.

The server serves a small, deliberately chosen surface on loopback; a tunnel
(configured by the operator) dials outward so nothing here binds a public
address. What a caller may do is decided in `policy`, re-checked at the moment
of every call rather than trusted from the tool listing, and the default is
refusal.

The first surface is copy: an assistant reads the posts a campaign has queued
without a caption yet - with the video, the attached product, the destination
platform and the campaign's own configuration for context - and writes the
caption, first comment and thread replies back. It never approves or publishes
them; that stays a person's decision, in the app.
"""
