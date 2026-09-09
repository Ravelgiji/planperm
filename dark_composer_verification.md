# Dark Composer Verification

The light sliver in the assistant composer came from nested Streamlit input wrapper elements that retained a white background while the inner textarea was dark. The dark composer now applies the same `#102024` surface to every wrapper between the assistant-card input root and textarea.

Browser inspection confirmed five nested composer layers now resolve to the same dark RGB colour (`rgb(16, 32, 36)`), and the rendered desktop control no longer shows a light left-edge artifact.

A tall mobile dark-mode review confirmed the assistant card stacks beneath the map and its composer retains the same continuous dark surface from left edge through the send action, with no light artifact visible.
