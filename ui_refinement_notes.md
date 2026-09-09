# Compact Hero and Theme Verification Notes

- The planning metrics now appear inside the terrain header: Applications, Approval rate, and Refused.
- The redundant `Nearby applications` expander and record list beneath the map have been removed. Map points remain the route to application details.
- A transient Streamlit loading state appears immediately following a restart while live planning data loads; the completed page contains the hero, source-linked map points, controls, and chat panel.
- The approval-rate interpolation has been corrected to show a clean percentage. Final validation remains focused on the dark-mode label (`Light mode` once enabled), contrast, and the redesigned map-point popup.
- The contextual theme label now reads `Dark mode` in the light workspace and `Light mode` after the dark workspace is enabled. In dark mode the hero title, summary metrics, navigation text, search and radius labels, map detail bar, and assistant panel were visibly legible.
- A live nearby marker opened the new planning record card. It presents the decision badge, record type, reference, address, application category and date, concise proposal excerpt, and a clear link to the original planning record.
