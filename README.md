# CLIProxyAPI Image Generation for Home Assistant

A small Home Assistant **AI Task** adapter for generating images through a local
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) server with a Gemini-compatible endpoint.

The integration exposes an image-generation AI Task entity which can be selected
by integrations such as **Activity Images** and used through Home Assistant's
`ai_task.generate_image` action.

## Features

- Native Home Assistant AI Task image generation.
- Select Gemini / Antigravity or ChatGPT through CLIProxyAPI, or a separate direct Google API profile.
- Refresh the provider model catalog and show recognized image-generation models only.
- Remember the selected model for each profile; never automatically fall back to paid Google API.
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

Open **Settings → Devices & services → CLIProxyAPI Image Generation → Configure**
to access **Select provider and model**, **Refresh models**, **Test connection**,
and **Configure Google API key**. Refresh and connection checks only retrieve
model catalogs; they do not generate images. Selecting a provider refreshes its
catalog. Models are remembered per profile. The existing AI Task entity and
configuration entry stay the same.

The CLIProxyAPI profiles share the existing proxy URL and proxy API key. The
proxy chooses the eligible account and applies its own routing and retry policy.
The adapter uses the exact advertised model ID; unprefixed IDs cannot guarantee
a particular upstream credential or provider if multiple routes offer the same
ID. Use explicitly configured proxy model prefixes if upstream isolation is
required.

The separate Google API profile uses the official HTTPS Gemini endpoint and
its own API key. Saving the key does not activate that profile. Selecting it
explicitly enables potentially billable generation; no automatic fallback to
it is performed.

Model catalogs frequently lack reliable output-modality metadata. The picker
conservatively recognizes supported Gemini image and GPT Image model families.
Unknown model aliases, Muse text/vision models, and OpenCode Go models are not
offered without verified image-generation support. Listing a model is not a
guarantee of account quota, authorization, or successful generation.

The adapter selects Gemini generateContent or OpenAI Images (including JSON
image edits with inline reference data) automatically. It never fetches a
provider-returned image URL. Existing installations retain their original
Gemini-compatible model until the operator selects a new route.

To change the **server URL or API key**, use **Reconfigure** on the same
integration. The new connection is tested before it replaces the saved one.
Leave the API key empty to retain the existing key. The existing config
entry and entity ID are kept.

The config flow verifies connectivity to `/v1/models` and a valid model-list
response (up to 1 MiB). Model names remain configurable, including aliases; the
flow does not generate an image to verify model capabilities. HTTP redirects are
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
