# Invoice paid stamp — 2026-10-14

Implemented in CustomerDisplayPage only, using an original imagegen asset with verified RGBA transparency in frontend/public/assets/invoice-paid-stamp.png.

The QR/countdown region shows a circular red PAID stamp only when payment status is paid AND printing_state is completed. The paid bill snapshot places the same stamp at the right-hand location previously occupied by the QR. Pending payment, submitted/processing/failed/unknown printing, and other-order events cannot stamp the invoice. Checkout, payment, printing and voice code were untouched by this UI change.

Verification: 517 frontend tests passed; isolated TypeScript/Vite production build passed. Chromium preview exercised real display reducer and views with synthetic events, at 375/768/1440px: paid/submitted had no stamp; verified completion showed stamp; another-order completion was ignored. All stamp bounding boxes fit the viewport. No browser page errors and no automated WCAG A/AA axe violations in the preview. No committed visual baseline exists, so visual regression verdict is INCONCLUSIVE; functional preview checks passed. Preview does not prove live merchant or physical printer behavior. Screenshots and detailed QA JSON: /tmp/invoice-paid-preview-_rs9kz0f (temporary).

PNGTree reference could not be fetched (403); the user-supplied reference guided a new generated asset. No third-party asset was downloaded into the source. No production restart, live transaction, physical print, commit or push was performed.

Self-evaluation: accuracy 4/5 (state guards and browser checks pass; live end-to-end untested), completeness 4/5 (both invoice views and responsive sizes covered; no committed visual baseline), clarity 4/5 (PAID marker plus existing inline print status; small-view layout still warrants live acceptance), actionability 4/5 (source and local asset ready; deployed static build not replaced), conciseness 4/5 (two source/test files plus one image; verification took longer than necessary). Overall 4.0/5. Highest-impact follow-up: inspect the actual invoice after the existing CUPS completion event on the deployed kiosk.
