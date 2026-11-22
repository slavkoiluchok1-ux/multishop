"""API гаманця: перегляд балансу за частинами й історії."""
from django.db.models import Sum
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FrozenPayout, Wallet
from .serializers import WalletSerializer, WalletTransactionSerializer
from .services import release_due_frozen


class WalletView(APIView):
    """GET /api/wallet — баланс за частинами та останні транзакції (лише читання)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        release_due_frozen(request.user)
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        transactions = wallet.transactions.all()[:50]
        # Найближчі заморожені виплати: сума й дата доступності
        payouts_qs = FrozenPayout.objects.filter(
            seller=request.user, status="frozen"
        )
        payouts = payouts_qs.order_by("release_at")
        frozen_payouts_total = payouts_qs.aggregate(total=Sum("amount_mc"))["total"] or 0
        frozen_payouts = [
            {"amount_mc": str(p.amount_mc), "release_at": p.release_at.strftime("%d.%m.%Y")}
            for p in payouts
        ]
        return Response(
            {
                "balance": WalletSerializer(wallet).data,
                "transactions": WalletTransactionSerializer(transactions, many=True).data,
                "frozen_payouts": frozen_payouts,
                "frozen_payouts_total": str(frozen_payouts_total),
            }
        )
