cask "rectangle" do
  version "0.93"
  sha256 "848817526f3f7bd41f73cce295582523ff7bb4746ed64723575659574f298a76"

  url "https://github.com/rxhanson/Rectangle/releases/download/v0.93/Rectangle0.93.dmg"
  name "Rectangle"
  desc "Rectangle App"
  homepage "https://github.com/rxhanson/Rectangle"

  app "Rectangle.app"
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Rectangle"
end
