cask "proxyman" do
  version "6.3.0"
  sha256 "7b9efa229588f86e2f999e278e3374d8e55d78735aa405dfa53f3d3da4cf093d"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.3.1/Proxyman-6.3.0.dmg"
  name "Proxyman"
  desc "Proxyman App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "Proxyman.app"
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{staged_path}/Proxyman.app"],
                   sudo: true
  end

  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Proxyman"
end
