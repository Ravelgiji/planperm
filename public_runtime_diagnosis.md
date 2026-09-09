# Public Streamlit Runtime Diagnosis

Observed on 4 September 2026:

- The managed preview at `https://8501-iz0lrzn51uoi5scl6c53e-98b00d8c.us1.manus.computer` loads the Streamlit workspace and its live planning data.
- The public domain `https://planperm-ui-hxb9rgwt.manus.space` renders Streamlit’s skeleton/loading frame indefinitely and never opens the workspace.
- Production logs show Streamlit starts and binds to `0.0.0.0:3000`, while the working managed development service uses port 8501.

The repair should align the container runtime with Streamlit’s standard web-socket configuration behind the public proxy and keep direct map clicks immediate, without candidate confirmation state.
