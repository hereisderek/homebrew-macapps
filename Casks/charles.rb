cask "charles" do
  version "5.0.2"
  sha256 "a497843bd7e38a644a960d3868be85fa5acd04438d6a8353959cb96f3f1e6c3b"

  url "https://github.com/hereisderek/homebrew-macapps/releases/download/v0.6.0/Charles-5.0.2.dmg"
  name "Charles"
  desc "Charles App"
  homepage "https://github.com/hereisderek/homebrew-macapps"

  app "Charles.app"
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Charles"
end
