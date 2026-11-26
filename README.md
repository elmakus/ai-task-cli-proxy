# CLIProxyAPI Image Generation for Home Assistant

A small Home Assistant **AI Task** adapter for generating images through a local
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) server with OpenAI Images and Gemini-compatible endpoints.

The integration exposes an image-generation AI Task entity which can be selected
by integrations such as **Activity Images** and used through Home Assistant's
`ai_task.generate_image` action.

## Features

- Native Home Assistant AI Task image generation.
- Select ChatGPT, Antigravity, Muse, OpenCode or Google Paid API through a single CLIProxyAPI connection.
- Refresh the provider model catalog and show recognized image-generation models only.
- Remember the selected model for each profile; never automatically fall back to paid Google API.
- Gemini-compatible `/v1beta/models/{model}:generateContent` endpoint.
- Optional reference-image attachments (up to 10 files and 20 MB total input).
- Change the image model later through **Options** without reinstalling.
- Change server URL or API key through **Reconfigure**, preserving the existing entity.
- HTTP response and image-size checks to prevent oversized provider payloads.
- Configurable proxy URL and API key at initial setup; default ChatGPT / GPT Image 2.6 Flare.
- Transparent PNG requested by default for GPT Image; optional opaque or automatic background.
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

At initial setup, enter the CLIProxyAPI server URL and API key. No separate
upstream API key is needed in Home Assistant. New entries and legacy entries
without an explicit provider select **ChatGPT / gpt-image-2.6-flare** by default.
The existing entity ID is unchanged. This intentionally replaces the old
implicit Gemini route; choose another provider explicitly if needed.

Open **Settings → Devices & services → CLIProxyAPI Image Generation → Configure**
for **Select provider and model**, **Refresh models**, **Test connection**,
and **Background**. Catalog checks do not generate images. Selected models are
remembered per provider. Background defaults to **transparent**; **opaque**
and **auto** are also available. GPT Image requests include the background
parameter and PNG output. The caller's prompt is preserved unchanged. Gemini
does not have a verified native alpha parameter, so this setting does not
guarantee transparency on that route. Images are returned unchanged, with no
background removal or format conversion.

All profiles use the same proxy URL and API key. The proxy selects eligible
accounts. For the two Gemini routes, configure and advertise unique proxy
prefixes: `antigravity/` for Antigravity and `google-paid/` for paid Google.
The paid profile accepts only `google-paid/gemini-3.1-flash-image`. Unprefixed
Gemini IDs are deliberately rejected because the catalog owner alone does
not establish which upstream account will be charged. No automatic provider
fallback is implemented by the adapter. Proxy routing must preserve isolation.

Only recognized supported image-generation families and matching provider
metadata are offered. Models explicitly declaring non-image output are excluded.
Muse and the current OpenCode Go plugin have no verified image transport and
therefore show no models until such support is implemented. OpenCode Console
image models are separate from the Go catalog. Listing a model does not prove
quota, authorization or successful generation. Unknown aliases are not offered.

The adapter selects Gemini generateContent or OpenAI Images (including JSON
image edits with inline reference data) automatically. It never fetches a
provider-returned image URL. Neither a production prompt change nor an
Activity Images integration update is required by this adapter.

To change the **server URL or API key**, use **Reconfigure** on the same
integration. The new connection is tested before it replaces the saved one.
Leave the API key empty to retain the existing key. The existing config
entry and entity ID are kept.

The config flow verifies connectivity to `/v1/models` and a valid model-list
response (up to 1 MiB). The flow does not generate an image to verify
model capabilities. HTTP redirects are
not followed. Use HTTPS if the connection crosses an untrusted network.

Generated images are limited to 25 MiB and 25 million pixels, responses to
40 MiB, and the total reference attachments to 20 MiB. Generated images must
be decodable, non-animated PNG, JPEG or WebP matching the reported MIME type.
Oversized or malformed responses result in a Home Assistant error instead of an
unbounded read. Requests are never automatically retried by this adapter; after
a timeout or malformed provider response, the outcome may be unknown.

## Tests

The adapter has isolated Python unit tests for both normal image generation
and error scenarios, including reference attachments, bounds, and option
changes. GitHub Actions runs the tests for Python 3.13 and 3.14 on pushes and
pull requests.

Run them locally with:

```sh
python -m pip install pytest aiohttp voluptuous pillow
python -m pytest -q tests/
```


Native AI Task integration and service tests use pinned HA 2026.11.0 fixtures
and synthetic HTTP responses. Run them separately from the lightweight tests
(which install HA stubs), using Python 3.14:

```sh
python -m pip install -r requirements-native-tests.txt
python -m pytest -p no:respx --asyncio-mode=auto -o asyncio_default_fixture_loop_scope=function -q tests_native/
```

No test requires a real CLIProxyAPI server, API credentials or paid generation.

## Source layout

- Integration: `custom_components/ai_task_cli_proxy/`
- Unit tests: `tests/`
- Native HA contract tests: `tests_native/`

This project is independently maintained for local Home Assistant use.
It is not affiliated with the upstream CLIProxyAPI project.
