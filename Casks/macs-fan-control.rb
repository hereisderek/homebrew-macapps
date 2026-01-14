cask "macs-fan-control" do
  version "1.5.20"
  sha256 "1e56155448791b3db2fbaf9ca6569a1f53f67908da6b5772d4b7f5d2d83a3169"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.1.0/MacsFanControl-1.5.20.dmg"
  name "Macs Fan Control"
  desc "Macs Fan Control App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "Macs Fan Control.app"
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{staged_path}/Macs Fan Control.app"],
                   sudo: true
  end
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Macs Fan Control"
end
