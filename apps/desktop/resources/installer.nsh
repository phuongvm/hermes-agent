!include "LogicLib.nsh"

!macro customPageAfterChangeDir
  Page custom hermesLegacyHklmPre
!macroend

!macro customHeader
  !ifndef BUILD_UNINSTALLER
    Function hermesGetInQuotes
      Exch $R0
      Push $R1
      Push $R2
      Push $R3

       StrCpy $R2 -1
       IntOp $R2 $R2 + 1
        StrCpy $R3 $R0 1 $R2
        StrCmp $R3 "" 0 +3
         StrCpy $R0 ""
         Goto Done
        StrCmp $R3 '"' 0 -5

       IntOp $R2 $R2 + 1
       StrCpy $R0 $R0 "" $R2

       StrCpy $R2 0
       IntOp $R2 $R2 + 1
        StrCpy $R3 $R0 1 $R2
        StrCmp $R3 "" 0 +3
         StrCpy $R0 ""
         Goto Done
        StrCmp $R3 '"' 0 -5

       StrCpy $R0 $R0 $R2
       Done:

      Pop $R3
      Pop $R2
      Pop $R1
      Exch $R0
    FunctionEnd

    Function hermesLegacyHklmPre
      ; 1. Only for per-user intent, outer (non-elevated) instance, and non-silent.
      ${If} $installMode != "CurrentUser"
        Abort            ; skip page
      ${EndIf}
      ${If} ${UAC_IsInnerInstance}
        Abort            ; skip page
      ${EndIf}
      ${If} ${Silent}
        Abort            ; skip page
      ${EndIf}
      ; 2. Detect legacy machine-wide install.
      ReadRegStr $R0 HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString
      ${If} $R0 == ""
        Abort
      ${EndIf}
      ReadRegStr $R1 HKLM "${UNINSTALL_REGISTRY_KEY}" DisplayVersion
      ReadRegStr $R2 HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
      ; 3. Prompt. /SD IDNO makes the default safe if a future silent path reaches here.
      MessageBox MB_YESNOCANCEL|MB_ICONEXCLAMATION|MB_DEFBUTTON1 \
        "A machine-wide Hermes $R1 is installed at $R2.$\r$\n$\r$\nRemove it now (administrator approval required) so shortcuts open the version you are installing?$\r$\n$\r$\nYes = remove (UAC prompt), No = keep it and continue, Cancel = quit installer" \
        /SD IDNO IDYES hermes_remove IDNO hermes_keep
      Quit                                        ; IDCANCEL
    hermes_remove:
      ; 4. Copy uninstaller out of its own directory (it deletes that directory), run elevated & silent.
      Push "$R0"
      Call hermesGetInQuotes
      Pop $R3
      ${If} $R3 == ""
        StrCpy $R3 "$R2\Uninstall ${PRODUCT_NAME}.exe"
      ${EndIf}
      CopyFiles /SILENT "$R3" "$PLUGINSDIR\legacy-uninstaller.exe"
      ExecShellWait "runas" "$PLUGINSDIR\legacy-uninstaller.exe" '/allusers /S /KEEP_APP_DATA --updated _?=$R2' SW_HIDE
      ; 5. Re-check; on failure fall through to the same keep/quit choice.
      ReadRegStr $R0 HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString
      ${If} $R0 != ""
        MessageBox MB_YESNO|MB_ICONSTOP "The machine-wide Hermes could not be removed (elevation declined or uninstaller failed).$\r$\n$\r$\nContinue installing for the current user anyway? Existing shortcuts will keep opening the old version." /SD IDNO IDYES hermes_keep
        Quit
      ${EndIf}
    hermes_keep:
      Abort            ; no UI page is shown; continue to INSTFILES
    FunctionEnd
  !endif
!macroend

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
