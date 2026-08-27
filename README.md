# AI Task CLI Proxy

Home Assistant AI Task adapter for image generation through a local
CLIProxyAPI-compatible Gemini endpoint.

The adapter supports reference-image attachments. Home Assistant resolves media
selector values to local files, and the adapter sends those files to Gemini as
inline image parts alongside the text prompt. It accepts up to 10 image files and
20 MB of source image data per request.

Private source repository for the locally maintained Home Assistant integration `ai_task_cli_proxy`.

- Deployment path: `/config/custom_components/ai_task_cli_proxy`
- Upstream: none; this is local integration code.
- Live version captured: `0.1.0` on 2026-08-27.
