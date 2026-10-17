# Repository ownership

Application code publishes to `https://github.com/vutiendat323/OpenJarvis.git`.
The Jarvis kiosk snapshot is on `feat/openmetek-kiosk`; other branches are
maintained independently. A workstation may keep a different local branch name
while tracking `origin/feat/openmetek-kiosk`.

The parent workspace is `https://github.com/homeslands/OpenMetek.git`. Its main
branch contains shared documentation, a launcher wrapper and pinned submodules
for OpenJarvis, Audio, Visual and Metric3D. Publish application commits here
first, then commit the OpenJarvis pointer in the parent.

OpenMetek's retained `feat/live-voice-mode` branch and
`archive/application-main-c55bda34` preserve the former application history.
They are historical snapshots, not the publication destination for new app code.
