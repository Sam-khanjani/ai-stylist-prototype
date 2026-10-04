# Assumptions

What this project assumes, and what a store would need to adapt before using it.

## Scope

- A production idea built on public data: it shows how an AI assistant, stylist and fitting room can improve the
  online shopping experience. It is not connected to any store's real systems.
- It is built the way a production system is, not as a throwaway demo:
  - **Scalable deployment:** the web app and the api run on Cloud Run, which adds instances with traffic and scales
    to zero without it. All infrastructure is defined in Terraform.
  - **CI/CD:** every push to `main` builds and deploys both services through GitHub Actions, with Workload Identity
    Federation instead of stored keys.
  - **Regression tests:** a golden dataset of 54 questions is run before a release and compared with the baseline,
    so a change that breaks an answer is caught before it ships.
  - **Monitoring:** the admin dashboard shows usage, fallbacks, thumbs-down answers, latency, cost, errors and eval
    history, so anomalies and regressions show up quickly; each answer links to its full trace in Langfuse.
  - **Security and privacy:** a private api, secrets in Secret Manager, least-privilege service accounts, EU-only
    processing and automatic data deletion.
- Many features need to be customized for a real store: the style advisor's rules, the occasions and their
  guides, the questions it asks, the tone of voice and the product filters were tuned on the public website and a
  sample of the catalog.
- No accounts, orders or payments. Questions about an order's status go to customer service.
- Menswear only: the catalog sample and the occasion guides are the men's collection.

## Data

- Only public data from suitsupply.com: product pages, customer service pages, store details and the occasion
  guides in the site's menu. No internal APIs, even where the site uses one (for example per-product size charts).
- Crawling respects robots.txt and waits a few seconds between pages. The data is used privately and is not
  included in the repository.
- The catalog is a snapshot of about 250 products, not the full range. Prices and availability can be out of date,
  and there is no live stock.
- One market: the Netherlands site in English (`en-nl`), so prices are in euros and policies are the Dutch ones.

## Language

- Search runs in English. Questions in other languages, or with typos, are rewritten into English first, and the
  answer is written in the customer's language.

## AI and privacy

- All AI processing stays in the EU: Vertex AI in europe-west4, or the `eu` multi-region for models not offered in
  a single EU region. The `global` endpoint is never used.
- Visitors are anonymous: a random id in a cookie, no names or emails. Chats are deleted after 30 days.
- Try-on photos are never stored. They are sent with the request, kept in memory while the image is made, and gone
  when the response is sent.
- Usage events keep no message text. Their link to a trace is removed after 30 days, and the events themselves after
  a year.

## Answers

- An answer may only state what its sources say. When the sources don't cover a question, the assistant says so
  and points to customer service rather than guessing.
- Style is the stylist's call: the judge checks facts, citations and the budget, not taste.
- For an occasion, the style advisor needs the occasion, its dress code, the season and, when it matters, the
  customer's role (groom or guest). For clothes they own, it needs the pieces, the look and the season. Budget and
  colours are optional. It asks at most two questions per turn.

## Sizing

- Sizes follow the standard EU system that the public product pages use (regular N = half the chest in cm, short
  N/2, long 2N−2), not Suitsupply's own size charts.
- Measurements from the photo come from pose landmarks scaled by the stated height. The calibration constants are
  estimates and have not been checked against real fittings.
- The advice is a starting point. The confidence drops when there is no photo, no weight, or the two disagree.
- Before real use, the measurements need to be calibrated against real customers' sizes, and the size logic
  replaced by the store's own size charts.

## Try-on

- The photo shows one adult, standing and facing the camera.
- At most two items at once, put on from the inside out. Accessories such as ties and belts aren't supported by
  the try-on model.
- The image shows how an item looks, not how it fits: the preferred fit only changes the size advice.
- Try-on is expensive (about $0.06–0.13 per image), so a store has to limit it: a daily limit per visitor (10 by
  default here), or only for signed-in customers or selected users.

## Scale and cost

- Runs on small settings to keep costs low: the smallest Cloud SQL tier, at most two instances per service, one
  region. Scaling up is a configuration change in Terraform, not a code change, but it hasn't been load tested.
- Try-on is the most expensive feature, which is why it has its own limit (see above).
- The chat has no rate limit yet. At scale, a rate limit per visitor (and per IP address) has to be added, so the
  chat can't be misused for spam, scraping or running up model costs. Until then, set a budget alert before sharing
  the link widely.
