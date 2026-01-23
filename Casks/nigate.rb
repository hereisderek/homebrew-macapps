cask "nigate" do
  version "1.3.4"
  sha256 "f549c29342f9e227d9a0b6190b50efe68bf0e2b1465873649e473a99273a1250"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.2.0/Nigate-1.3.4.dmg"
  name "Nigate"
  desc "Nigate App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "Nigate.app"
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{staged_path}/Nigate.app"],
                   sudo: true
  end
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Nigate"
end
