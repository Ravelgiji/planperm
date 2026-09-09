# Search and Assistant Verification

The site-search placeholder now reads **Town or address** and the user-facing error message no longer suggests Eircode lookup. In dark mode, a typed assistant question remained legible within the chat composer before submission.

The assistant card uses a fixed 570-pixel desktop height, matching the map height, and reserves an independently scrollable 330-pixel conversation region above the composer. On mobile, its fixed card height is reduced to 500 pixels to preserve usable page flow.

The simplified search was verified with `Salthill, Galway`: the selected site changed from Galway, Ireland to Salthill, Galway, nearby planning records refreshed, and the assistant context updated to the selected place. The dark-mode composer displayed typed text clearly against the dark card surface.

The long-question test overflowed the fixed-height assistant history while the composer stayed visible at the bottom of the card. The conversation region scrolled independently from its bottom position back to the first lines of the test message, confirming that extended context is contained without increasing the card height.
