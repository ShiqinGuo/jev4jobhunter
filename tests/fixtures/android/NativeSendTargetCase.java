package jobhunter;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Anonymous geometry observed in BOSS 14.170, including its unlabeled send icon. */
public final class NativeSendTargetCase {
    private static SendTarget.Candidate node(String type, boolean clickable, boolean visible, int left, int top, int right, int bottom) {
        return new SendTarget.Candidate(type,clickable,visible,left,top,right,bottom);
    }
    private static void rejected(List<SendTarget.Candidate> nodes) {
        try { SendTarget.choose(nodes,962,1372,1717); throw new AssertionError("Ambiguous/missing target accepted"); }
        catch (IllegalArgumentException expected) { }
    }
    public static void main(String[] args) {
        SendTarget.Candidate send = node("android.widget.ImageView",true,true,1106,1619,1204,1717);
        List<SendTarget.Candidate> controls = Arrays.asList(
            node("android.widget.LinearLayout",true,true,984,1619,1080,1717),
            node("com.hpbr.bosszhipin.widget.MEmotionView",false,true,984,1619,1080,1717),
            node("android.widget.ImageView",true,true,20,1619,120,1717),
            node("android.widget.ImageView",true,true,1106,800,1204,900),send);
        if (SendTarget.choose(controls,962,1372,1717)!=4) throw new AssertionError("Wrong send icon");
        rejected(new ArrayList<SendTarget.Candidate>());
        rejected(Arrays.asList(send,send));
        rejected(Arrays.asList(node("android.widget.ImageView",true,false,1106,1619,1204,1717)));
        rejected(Arrays.asList(node("android.widget.ImageView",false,true,1106,1619,1204,1717)));
        rejected(Arrays.asList(node("android.widget.ImageView",true,true,1106,1619,1106,1717)));
        System.out.println("PASS: unlabeled icon, emoji, attachment, outside composer, missing, ambiguous, hidden, disabled, empty bounds");
    }
}
