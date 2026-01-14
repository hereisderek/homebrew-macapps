cask "display-buddy" do
  version "3.1.0"
  sha256 "d9fcb14747dc6fca974dcd914901397b7dbf65dab29cf80571801eb5c2d9a2e2"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.3.1/DisplayBuddy-3.1.0.dmg"
  name "DisplayBuddy"
  desc "DisplayBuddy App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "DisplayBuddy.app"
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{staged_path}/DisplayBuddy.app"],
                   sudo: true
  end

  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/DisplayBuddy"
end
