cask "daisy-disk" do
  version "4.33.2"
  sha256 "acee0b30898c29a877c46ca77eb67c8fd5b312389f8b2415ab6d565fd6c7db88"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.1.0/DaisyDisk-4.33.2.dmg"
  name "DaisyDisk"
  desc "DaisyDisk App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "DaisyDisk.app"
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/DaisyDisk"
end
