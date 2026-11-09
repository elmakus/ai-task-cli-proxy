# CLIProxyAPI Image Generation for Home Assistant

A small Home Assistant **AI Task** adapter for generating images through a local
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) server with a Gemini-compatible endpoint.

The integration exposes an image-generation AI Task entity which can be selected
by integrations such as **Activity Images** and used through Home Assistant's
`ai_task.generate_image` action.

## Features

- Native Home Assistant AI Task image generation.
- Gemini-compatible `/v1beta/models/{model}:generateContent` endpoint.
- Optional reference-image attachments (up to 10 files and 20 MB total input).
- Change the image model later through **Options** without reinstalling.
- Change server URL or API key through **Reconfigure**, preserving the existing entity.
- HTTP response and image-size checks to prevent oversized provider payloads.
- Configurable API server URL, API key, and model at initial setup.
- On-demand requests with a 180-second timeout.

## Installation via Home Assistant Marketplace (2026.11+)

The repository must be **public** to be added as a custom Marketplace repository.
After making it public:

1. In Home Assistant, open **Settings → Marketplace → Add from link**.
2. Enter `https://github.com/elmakus/ai-task-cli-proxy` as the repository URL.
3. Install **CLIProxyAPI Image Generation**, and restart Home Assistant if requested.
4. Go to **Settings → Devices & services** to configure it, if this is a new installation.

When migrating an existing manual installation, keep the existing integration
configuration entry. Avoid deleting it or changing its entity ID; consumers
may reference `ai_task.image_generation`.

## Configuration

At initial setup, enter the CLIProxyAPI server URL, API key, and the
Gemini-compatible image model. The server must be reachable from Home Assistant.

To change the **image model**, go to **Settings → Devices & services →
CLIProxyAPI Image Generation → Configure** (Options Flow). The next image
request uses the new model; the existing AI Task entity ID is preserved.

To change the **server URL or API key**, use **Reconfigure** on the same
integration. The new connection is tested before it replaces the saved one.
Leave the API key empty to retain the existing key. The existing config
entry and entity ID are kept.

The config flow verifies connectivity to `/v1/models`; it does not
independently validate that the selected model supports image generation.

Generated images are limited to 25 MB, responses to 40 MB, and the total
reference attachments to 20 MB. Oversized or malformed responses result in
a Home Assistant error instead of an unbounded read.

## Tests

The adapter has isolated Python unit tests for both normal image generation
and error scenarios, including reference attachments, bounds, and option
changes. GitHub Actions runs the tests for Python 3.13 and 3.14 on pushes and
pull requests.

Run them locally with:

```sh
python -m pip install pytest aiohttp voluptuous
python -m pytest -q tests/
```


## Source layout

- Integration: `custom_components/ai_task_cli_proxy/`
- Unit tests: `tests/`

This project is independently maintained for local Home Assistant use.
It is not affiliated with the upstream CLIProxyAPI project.
