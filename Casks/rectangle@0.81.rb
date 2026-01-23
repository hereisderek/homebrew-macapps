cask "rectangle@0.81" do
  version "0.81"
  sha256 "a19673e9bb201bee2579036ad82fa72da00efd893e3a44ba06794c4078e52bd1"

  url "https://github.com/rxhanson/Rectangle/releases/download/v0.81/Rectangle0.81.dmg"
  name "Rectangle"
  desc "Rectangle App"
  homepage "https://github.com/rxhanson/Rectangle"

  app "Rectangle.app"
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/Rectangle"
end
