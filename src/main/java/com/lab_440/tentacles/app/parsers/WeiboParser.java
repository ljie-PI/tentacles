package com.lab_440.tentacles.app.parsers;

import com.lab_440.tentacles.app.items.WeiboItem;
import com.lab_440.tentacles.slave.parser.ParseRule;
import com.lab_440.tentacles.slave.parser.BaseParser;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.Iterator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class WeiboParser extends BaseParser {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private int maxPage = 10;
    private Pattern searchPagePtn = Pattern.compile("page=(\\d+)");
    private Pattern tsPtn = Pattern.compile("&_=\\d{13}");
    private Pattern fansNumPtn = Pattern.compile("(\\d+)万");

    @ParseRule(priority = 1, uriPattern = "http://m.weibo.cn/page/pageJson.*")
    public void parseSearchResult() {
        JsonObject pageObj = new JsonObject(getPage());
        JsonArray cardsArr = pageObj.getJsonArray("cards", null);
        if (cardsArr == null) return;
        Iterator<Object> cardGrpIter = cardsArr.iterator();
        while (cardGrpIter.hasNext()) {
            JsonObject cardGrpObj = (JsonObject) cardGrpIter.next();
            String cardGrpTypeStr = cardGrpObj.getValue("card_type", 0).toString();
            int cardGrpType = Integer.parseInt(cardGrpTypeStr);
            if (cardGrpType != 11) continue;
            JsonArray cards = cardGrpObj.getJsonArray("card_group", null);
            if (cards == null) continue;
            Iterator<Object> cardIter = cards.iterator();
            while (cardIter.hasNext()) {
                JsonObject cardObj = (JsonObject) cardIter.next();
                String cardType = cardObj.getValue("card_type", 0).toString();
                if (cardType == null || Integer.parseInt(cardType) != 9) {
                    continue;
                }
                WeiboItem item = fillWeiboItem(cardObj.getJsonObject("mblog", null), true);
                if (item != null) {
                    addItem(item);
                }
            }
        }
        Matcher pageMatcher = searchPagePtn.matcher(getUrl());
        if (pageMatcher.find()) {
            int pageNum = Integer.parseInt(pageMatcher.group(1));
            if (pageNum < maxPage) {
                String newUrl = pageMatcher.replaceAll("page="+ String.valueOf(pageNum + 1));
                Matcher tsMatcher = tsPtn.matcher(newUrl);
                String tsParam = "&_=" + System.currentTimeMillis();
                if (tsMatcher.find()) {
                    newUrl = tsMatcher.replaceAll(tsParam);
                } else {
                    newUrl += tsParam;
                }
                followUrl(newUrl);
            }
        }
    }

    private WeiboItem fillWeiboItem(JsonObject tweetObj, Boolean followRetweet) {
        if (tweetObj == null)
            return null;
        WeiboItem item = new WeiboItem();
        item.setId(tweetObj.getString("idstr", "0"))
                .setCreatedTs(tweetObj.getLong("created_timestamp", 0l))
                .setText(tweetObj.getString("text", ""));
        JsonObject userObj = tweetObj.getJsonObject("user", null);
        if (userObj != null) {
            item.setUserId(userObj.getLong("id", 0l))
                    .setUserDispName(userObj.getString("screen_name"))
                    .setUserVerified(userObj.getBoolean("verified"));
            String fansNumStr = userObj.getValue("fansNum", "0").toString();
            Matcher matcher = fansNumPtn.matcher(fansNumStr);
            if (matcher.find()) {
                fansNumStr = matcher.replaceAll("$10000");
            }
            item.setUserFansNum(Integer.parseInt(fansNumStr));
        }
        if (followRetweet) {
            JsonObject retweetObj = tweetObj.getJsonObject("retweeted_status", null);
            item.setRetweet(fillWeiboItem(retweetObj, false));
        }
        return item;
    }
}
