/**
 * after-pack.mjs — electron-builder afterPack hook.
 *
 * Windows: stamps the Hermes icon + identity onto the packed Windows Hermes.exe via
 * rcedit (delegated to set-exe-identity.mjs).
 * macOS: restores the empty app-level localizations dropped during Electron extraction.
 */
import { mkdir, readdir } from 'node:fs/promises'
import path from "node:path"
import { stampExeIdentity } from "./set-exe-identity.mjs"

export default async function afterPack(context) {
  const { electronPlatformName, appOutDir, packager } = context
  if (electronPlatformName === "win32") {
    const productName = packager?.appInfo?.productFilename || "Hermes"
    const exe = path.join(appOutDir, `${productName}.exe`)
    const desktopRoot = path.resolve(import.meta.dirname, "..")
    await stampExeIdentity(exe, desktopRoot)
    return
  }

  if (electronPlatformName === 'darwin') {
    try {
      const resources = packager.getResourcesDir(appOutDir)
      const framework = packager.getMacOsElectronFrameworkResourcesDir(appOutDir)
      const entries = await readdir(framework, { withFileTypes: true })
      const locales = entries.filter(
        entry =>
          entry.isDirectory() && entry.name.endsWith('.lproj') && !/_(FEMININE|MASCULINE|NEUTER)\.lproj$/.test(entry.name)
      )
      await Promise.all(locales.map(entry => mkdir(path.join(resources, entry.name), { recursive: true })))
    } catch (error) {
      console.warn(
        `[after-pack] macOS locale markers were not restored: ${error instanceof Error ? error.message : String(error)}`
      )
    }
  }
}
}
