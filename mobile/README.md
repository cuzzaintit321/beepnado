# BeepNado mobile — Android and iOS

Native React Native/Expo client for the existing BeepNado service. This is an initial source release, not a signed or store-approved binary.

## First deploy the matching backend

The new /api/mobile/login endpoint is required. On yeet, back up the current app/main.py, obtain the matching version from this repository, rebuild BeepNado using Docker Compose, and verify /health/ready. Do not delete volumes or replace .env. Existing cookie-based browser sessions still work.

## Run on Fedora and test on Android

Use Node.js 22.13 or newer. In this directory:

```bash
npm install
npx expo install --fix
npx expo-doctor
npx expo start --tunnel
```

Install the matching Expo Go version on your phone and scan the QR code. Sign in using an existing verified BeepNado account. No account passwords or server credentials belong in this repository.

## Create an installable Android APK

```bash
npx eas-cli@latest login
npx eas-cli@latest build:configure
npx eas-cli@latest build --platform android --profile preview
```

EAS requires an Expo account and signing configuration. The preview build is an APK; production builds are AAB for Google Play. For iOS, configure Apple signing and use the production build profile. Neither store registration nor submission has been done.

## Included

- Verified-account sign-in, bearer sessions stored in Expo SecureStore, server-side logout and CSRF-protected writes.
- Real monitor status, sampled 30-day check pass rates and response times.
- Monitor details, manual checks and website monitor creation with authorization confirmation.
- Incident history, alert feed, public status link and persistent Arm/Disarm email setting.
- Pull to refresh and foreground polling. Monitoring and emails run on the existing server, not the phone.

## Before public release

Test on physical Android and iOS devices, align package dependencies with the chosen Expo SDK and commit a lockfile, provide final branded store icons and screenshots, complete privacy disclosures and account deletion requirements, configure signing, and perform a security review. Native push notifications, widgets, in-app billing and native creation/editing of every monitor type are not implemented. Email alerts remain the supported background notification channel; SMTP must be operational. App signing and store publishing require owner accounts.
