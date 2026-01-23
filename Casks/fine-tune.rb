cask "fine-tune" do
  version "1.0.0"
  sha256 "6b67f13a2414f58e85ef50380dce16ae790a1fe2c47abb30c95fc2942d8e6a51"

  url "https://github.com/ronitsingh10/FineTune/releases/download/v1.0.0/FineTune-1.0.0.dmg"
  name "FineTune"
  desc "FineTune App"
  homepage "https://github.com/ronitsingh10/FineTune"

  app "FineTune.app"
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{staged_path}/FineTune.app"],
                   sudo: true
  end
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/FineTune"
end
