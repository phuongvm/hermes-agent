!macro customInstall
  DetailPrint "Configuring Team Hermes Gateway..."
  CreateDirectory "$APPDATA\Hermes"
  IfFileExists "$APPDATA\Hermes\connections.json" seed_exists seed_missing

seed_missing:
  SetOutPath "$APPDATA\Hermes"
  File "/oname=connections.json" "${BUILD_RESOURCES_DIR}\default-connections.json"
  DetailPrint "Successfully initialized Hermes Gateway connection."
  Goto seed_done

seed_exists:
  DetailPrint "Existing Hermes Gateway configuration found, skipping seed."

seed_done:
!macroend
